"""
实训室选报系统 - 意向填报接口（最小可运行版）

启动（在 sxsxb 目录下）：
    $env:DB_PASSWORD='<你的密码>'
    python -m uvicorn filing_api:app --host 127.0.0.1 --port 8000

依赖：fastapi, uvicorn, pymysql（已安装）

鉴权：POST /api/auth/login 获取 Bearer Token；其余接口需 Authorization: Bearer <token>。
业务规则：BR-03 时间窗校验、BR-04 意向上限（首轮 3 / 补录 1）、BR-05 平行意向去重、
BR-06 提交即锁定、BR-07 不可撤回重填。
"""

import asyncio
import base64
import hashlib
import hmac
import json
import logging
import os
import re
import time
from datetime import datetime, timedelta
from typing import List, Optional

import pymysql
import uvicorn
from fastapi import Depends, FastAPI, Header, HTTPException
from fastapi.responses import JSONResponse, RedirectResponse
from fastapi.staticfiles import StaticFiles
from pymysql.cursors import DictCursor
from pydantic import BaseModel

DB_CONFIG = {
    "host": os.getenv("DB_HOST", "127.0.0.1"),
    "port": int(os.getenv("DB_PORT", "3306")),
    "user": os.getenv("DB_USER", "root"),
    "password": os.getenv("DB_PASSWORD", ""),
    "database": os.getenv("DB_NAME", "sxsxb"),
    "charset": "utf8mb4",
    "cursorclass": DictCursor,
    "autocommit": False,
}

TOKEN_SECRET = os.getenv("TOKEN_SECRET", "sxsxb-demo-secret").encode("utf-8")
TOKEN_TTL_SECONDS = 2 * 60 * 60
REMINDER_POLL_SECONDS = int(os.getenv("REMINDER_POLL_SECONDS", "30"))
ADMISSION_EXPIRY_POLL_SECONDS = int(os.getenv("ADMISSION_EXPIRY_POLL_SECONDS", "60"))


class UTF8JSONResponse(JSONResponse):
    media_type = "application/json; charset=utf-8"


app = FastAPI(title="实训室选报系统 - 填报接口", version="0.1.0",
              default_response_class=UTF8JSONResponse)


def get_conn():
    return pymysql.connect(**DB_CONFIG)


def issue_token(account_id: int) -> str:
    expires = int(time.time()) + TOKEN_TTL_SECONDS
    payload = f"{account_id}.{expires}"
    sig = hmac.new(TOKEN_SECRET, payload.encode("utf-8"), hashlib.sha256).hexdigest()[:32]
    return base64.urlsafe_b64encode(f"{payload}.{sig}".encode("utf-8")).decode("ascii")


def parse_token(token: str) -> int:
    try:
        raw = base64.urlsafe_b64decode(token.encode("ascii")).decode("utf-8")
        account_id, expires, sig = raw.split(".")
        payload = f"{account_id}.{expires}"
        expected = hmac.new(TOKEN_SECRET, payload.encode("utf-8"), hashlib.sha256).hexdigest()[:32]
        if not hmac.compare_digest(sig, expected) or int(expires) < int(time.time()):
            raise ValueError
        return int(account_id)
    except Exception:
        raise HTTPException(status_code=401, detail="登录已失效，请重新登录")


def current_user(authorization: Optional[str] = Header(default=None)) -> dict:
    if not authorization or not authorization.startswith("Bearer "):
        raise HTTPException(status_code=401, detail="缺少访问令牌")
    account_id = parse_token(authorization[7:].strip())
    conn = get_conn()
    try:
        with conn.cursor() as cur:
            cur.execute(
                """
                SELECT a.account_id, a.user_type, a.status,
                       s.student_id, s.student_no, s.name AS student_name,
                       t.teacher_id, t.teacher_no, t.name AS teacher_name
                FROM account a
                LEFT JOIN student s ON s.account_id = a.account_id
                LEFT JOIN teacher t ON t.account_id = a.account_id
                WHERE a.account_id = %s
                """,
                (account_id,),
            )
            row = cur.fetchone()
    finally:
        conn.close()
    if not row or row["status"] != "ACTIVE":
        raise HTTPException(status_code=401, detail="账号不存在或已停用")
    return row


def current_student(user: dict = Depends(current_user)) -> dict:
    if user["user_type"] != "STUDENT" or not user["student_id"]:
        raise HTTPException(status_code=403, detail="仅学生账号可访问该接口")
    return user


def current_teacher(user: dict = Depends(current_user)) -> dict:
    if user["user_type"] != "TEACHER" or not user["teacher_id"]:
        raise HTTPException(status_code=403, detail="仅老师账号可访问该接口")
    return user


def teacher_roles(cur, teacher_id: int) -> List[dict]:
    cur.execute(
        """
        SELECT role_code, scope_lab_id FROM teacher_role
        WHERE teacher_id = %s AND status = 'ACTIVE'
        """,
        (teacher_id,),
    )
    return cur.fetchall()


def teacher_lab_scope(cur, teacher_id: int):
    """教师数据可见范围：统筹岗返回全部，否则返回负责人岗位与在职导师所属实训室集合。"""
    roles = teacher_roles(cur, teacher_id)
    if any(r["role_code"] == "COORDINATOR" for r in roles):
        return True, set()
    scope_labs = {
        r["scope_lab_id"] for r in roles
        if r["role_code"] == "LAB_OWNER" and r["scope_lab_id"]
    }
    cur.execute(
        "SELECT lab_id FROM mentor WHERE teacher_id = %s AND status = 'ACTIVE'",
        (teacher_id,),
    )
    scope_labs.update(row["lab_id"] for row in cur.fetchall())
    return False, scope_labs


def require_lab_manager(cur, user: dict, lab_id: int) -> None:
    roles = teacher_roles(cur, user["teacher_id"])
    if any(r["role_code"] == "COORDINATOR" for r in roles):
        return
    if any(r["role_code"] == "LAB_OWNER" and r["scope_lab_id"] == lab_id for r in roles):
        return
    raise HTTPException(status_code=403, detail="仅该实训室负责人或全院统筹岗可操作")


def can_manage_interview(cur, user: dict, lab_id: int, intent_id: int) -> bool:
    """该生面试操作权：学生所选导师，或本室负责人/统筹岗。"""
    roles = teacher_roles(cur, user["teacher_id"])
    if any(r["role_code"] == "COORDINATOR" for r in roles):
        return True
    if any(r["role_code"] == "LAB_OWNER" and r["scope_lab_id"] == lab_id for r in roles):
        return True
    cur.execute(
        """
        SELECT 1 FROM intent i
        JOIN mentor m ON m.mentor_id = i.mentor_id
        WHERE i.intent_id = %s AND m.teacher_id = %s
        LIMIT 1
        """,
        (intent_id, user["teacher_id"]),
    )
    return cur.fetchone() is not None


def find_session_participant(cur, session_id: int, student_id: int,
                             intent_id: Optional[int]) -> dict:
    """同一学生可在同一场次有多个意向（同室不同导师），按 intentId 精确定位。"""
    if intent_id is not None:
        cur.execute(
            """
            SELECT participant_id, intent_id, checkin_status
            FROM interview_participant
            WHERE session_id = %s AND intent_id = %s AND student_id = %s
            FOR UPDATE
            """,
            (session_id, intent_id, student_id),
        )
        participant = cur.fetchone()
        if not participant:
            raise HTTPException(status_code=404, detail="该意向不在本场次名单中")
        return participant
    cur.execute(
        """
        SELECT participant_id, intent_id, checkin_status
        FROM interview_participant
        WHERE session_id = %s AND student_id = %s
        FOR UPDATE
        """,
        (session_id, student_id),
    )
    rows = cur.fetchall()
    if not rows:
        raise HTTPException(status_code=404, detail="该学生不在本场次名单中")
    if len(rows) > 1:
        raise HTTPException(status_code=400, detail="该学生在本场次有多个意向，请指定意向后再操作")
    return rows[0]


def mentor_scope(cur, user: dict, lab_id: int) -> Optional[int]:
    """读取范围：None=全室（统筹岗/负责人），int=仅本人名下学生（导师）。"""
    roles = teacher_roles(cur, user["teacher_id"])
    if any(r["role_code"] == "COORDINATOR" for r in roles):
        return None
    if any(r["role_code"] == "LAB_OWNER" and r["scope_lab_id"] == lab_id for r in roles):
        return None
    cur.execute(
        """
        SELECT mentor_id FROM mentor
        WHERE teacher_id = %s AND lab_id = %s AND status = 'ACTIVE'
        LIMIT 1
        """,
        (user["teacher_id"], lab_id),
    )
    row = cur.fetchone()
    if row:
        return row["mentor_id"]
    raise HTTPException(status_code=403, detail="仅本实训室负责人或导师可查看")


def write_audit(cur, account_id: int, role: Optional[str], action: str,
                biz_type: str, biz_id: Optional[int],
                before: Optional[dict] = None, after: Optional[dict] = None) -> None:
    cur.execute(
        """
        INSERT INTO audit_log
          (operator_account_id, operator_role, action_code, biz_type, biz_id,
           before_value, after_value)
        VALUES (%s, %s, %s, %s, %s, %s, %s)
        """,
        (
            account_id,
            role,
            action,
            biz_type,
            biz_id,
            json.dumps(before, ensure_ascii=False) if before is not None else None,
            json.dumps(after, ensure_ascii=False) if after is not None else None,
        ),
    )


def parse_dt(value: str) -> datetime:
    try:
        return datetime.fromisoformat(value.replace("Z", "+00:00")).replace(tzinfo=None)
    except Exception:
        raise HTTPException(status_code=400, detail=f"时间格式错误：{value}")


def require_coordinator(cur, user: dict) -> None:
    roles = teacher_roles(cur, user["teacher_id"])
    if not any(r["role_code"] == "COORDINATOR" for r in roles):
        raise HTTPException(status_code=403, detail="仅全院统筹岗可执行提醒派发")


def ensure_supplement_eligible(cur, student_id: int, round_type: str) -> None:
    """BR-17：仅未录取学生可参加补录轮次填报。"""
    if round_type not in ("SECOND", "THIRD"):
        return
    cur.execute(
        """
        SELECT 1 FROM admission
        WHERE student_id = %s
          AND status IN ('OCCUPIED','CONFIRMED','EFFECTIVE','ENROLLED')
        LIMIT 1
        """,
        (student_id,),
    )
    if cur.fetchone():
        raise HTTPException(status_code=409, detail="你已被拟录取或确认录取，不能参加补录")


def pick_mentor(cur, lab_id: int, exclude_mentor_id: Optional[int] = None) -> Optional[int]:
    """按当前带生数升序选择仍有录取余额的导师；capacity 为空视为不限。"""
    cur.execute(
        """
        SELECT m.mentor_id, m.capacity,
               (SELECT COUNT(*) FROM admission a
                WHERE a.mentor_id = m.mentor_id
                  AND a.status IN ('OCCUPIED','CONFIRMED','EFFECTIVE','ENROLLED')) AS load_cnt
        FROM mentor m
        WHERE m.lab_id = %s AND m.status = 'ACTIVE'
          AND (%s IS NULL OR m.mentor_id <> %s)
          AND (m.capacity IS NULL OR
               (SELECT COUNT(*) FROM admission a
                WHERE a.mentor_id = m.mentor_id
                  AND a.status IN ('OCCUPIED','CONFIRMED','EFFECTIVE','ENROLLED')) < m.capacity)
        ORDER BY load_cnt ASC, m.mentor_id ASC
        LIMIT 1
        """,
        (lab_id, exclude_mentor_id, exclude_mentor_id),
    )
    row = cur.fetchone()
    return row["mentor_id"] if row else None


def ensure_mentor_capacity(cur, mentor_id: int, mentor_name: Optional[str] = None) -> None:
    """校验导师录取余额：仅限制最终录取人数，不限制学生填报意向数。"""
    cur.execute("SELECT capacity FROM mentor WHERE mentor_id = %s FOR UPDATE", (mentor_id,))
    row = cur.fetchone()
    if not row or row["capacity"] is None:
        return
    cur.execute(
        """
        SELECT COUNT(*) AS cnt FROM admission
        WHERE mentor_id = %s AND status IN ('OCCUPIED','CONFIRMED','EFFECTIVE','ENROLLED')
        """,
        (mentor_id,),
    )
    if cur.fetchone()["cnt"] >= row["capacity"]:
        who = f"导师{mentor_name}" if mentor_name else "该导师"
        raise HTTPException(
            status_code=409,
            detail=f"{who}录取人数已达上限（{row['capacity']} 人），请更换导师或调整上限",
        )


def lab_remaining(cur, lab_id: int) -> int:
    cur.execute("SELECT quota FROM lab WHERE lab_id = %s", (lab_id,))
    row = cur.fetchone()
    if not row:
        return 0
    return row["quota"] - active_admission_count(cur, lab_id)


def create_adjust_admission(cur, *, round_id: int, lab_id: int, student_id: int,
                            mentor_id: int, intent_id: Optional[int], source_type: str,
                            score: Optional[float], operator_teacher_id: int,
                            operator_account_id: int, remark: str) -> dict:
    """调剂/约谈落库：结论 + 预占名额 + 流水 + 通知 + 留痕。"""
    cur.execute("SELECT quota, name FROM lab WHERE lab_id = %s FOR UPDATE", (lab_id,))
    lab_row = cur.fetchone()
    if not lab_row:
        raise HTTPException(status_code=404, detail="实训室不存在")
    before = active_admission_count(cur, lab_id)
    if before >= lab_row["quota"]:
        raise HTTPException(status_code=409, detail=f"{lab_row['name']}名额已满")
    ensure_mentor_capacity(cur, mentor_id)
    cur.execute(
        """
        SELECT COALESCE(MAX(rank_no), 0) + 1 AS next_rank
        FROM selection_result
        WHERE round_id = %s AND lab_id = %s AND list_type = 'PROPOSED'
        """,
        (round_id, lab_id),
    )
    rank_no = cur.fetchone()["next_rank"]
    cur.execute(
        """
        INSERT INTO selection_result
          (round_id, lab_id, student_id, intent_id, list_type, rank_no,
           score, generated_by, remark)
        VALUES (%s, %s, %s, %s, 'PROPOSED', %s, %s, %s, %s)
        """,
        (round_id, lab_id, student_id, intent_id, rank_no, score, operator_teacher_id, remark),
    )
    result_id = cur.lastrowid
    cur.execute(
        """
        INSERT INTO admission
          (student_id, lab_id, mentor_id, round_id, source_type,
           confirm_type, status, confirm_deadline, confirmed_at, reason)
        VALUES (%s, %s, %s, %s, %s, 'AUTO', 'CONFIRMED', NULL, NOW(3), %s)
        """,
        (student_id, lab_id, mentor_id, round_id, source_type, remark),
    )
    admission_id = cur.lastrowid
    cur.execute(
        "UPDATE student SET selection_state = 'CONFIRMED' WHERE student_id = %s",
        (student_id,),
    )
    after = active_admission_count(cur, lab_id)
    cur.execute(
        """
        INSERT INTO lab_quota_log
          (lab_id, round_id, change_type, delta, active_count_before,
           active_count_after, admission_id, operator_id, remark)
        VALUES (%s, %s, 'OCCUPY', 1, %s, %s, %s, %s, %s)
        """,
        (lab_id, round_id, before, after, admission_id, operator_teacher_id, remark[:255]),
    )
    cur.execute("SELECT name FROM teacher WHERE teacher_id = %s", (mentor_id,))
    mentor_row = cur.fetchone()
    notify_student(
        cur,
        student_id,
        "调剂录取已自动确认",
        (
            f"经统筹调剂，你被{lab_row['name']}（导师：{mentor_row['name'] if mentor_row else mentor_id}）"
            "录取，系统已自动确认录取，无需再次操作。"
        ),
        "ADMISSION",
        admission_id,
    )
    write_audit(
        cur,
        operator_account_id,
        "TEACHER",
        "ADJUST_PROPOSE",
        "SELECTION_RESULT",
        result_id,
        None,
        {
            "round_id": round_id,
            "student_id": student_id,
            "lab_id": lab_id,
            "mentor_id": mentor_id,
            "source_type": source_type,
            "admission_id": admission_id,
            "status": "CONFIRMED",
            "confirm_type": "AUTO",
        },
    )
    return {"resultId": result_id, "admissionId": admission_id, "rankNo": rank_no}


def scheme_for_round(cur, round_type: str) -> dict:
    cur.execute(
        """
        SELECT * FROM score_scheme
        WHERE status = 'ACTIVE' AND (round_type = %s OR round_type IS NULL)
        ORDER BY (round_type IS NULL), scheme_id DESC
        LIMIT 1
        """,
        (round_type,),
    )
    row = cur.fetchone()
    if not row:
        raise HTTPException(status_code=500, detail="未配置可用的评分方案")
    return row


def active_admission_count(cur, lab_id: int) -> int:
    cur.execute(
        """
        SELECT COUNT(*) AS c FROM admission
        WHERE lab_id = %s AND status IN ('OCCUPIED','CONFIRMED','EFFECTIVE','ENROLLED')
        """,
        (lab_id,),
    )
    return cur.fetchone()["c"]


def score_summary(cur, session_id: int, intent_id: int) -> dict:
    cur.execute(
        """
        SELECT COUNT(*) AS judge_count,
               ROUND(AVG(total_score), 2) AS avg_total,
               ROUND(AVG(learning_score), 2) AS avg_learning,
               ROUND(AVG(logic_score), 2) AS avg_logic,
               ROUND(AVG(programming_score), 2) AS avg_programming,
               ROUND(AVG(time_score), 2) AS avg_time
        FROM interview_score
        WHERE session_id = %s AND intent_id = %s AND status = 'SUBMITTED'
        """,
        (session_id, intent_id),
    )
    return cur.fetchone()


def release_admission(cur, admission_id: int, operator_id: int, reason: str,
                      status: str = "RELEASED") -> bool:
    if status not in ("RELEASED", "ABANDONED"):
        raise ValueError("status 仅支持 RELEASED/ABANDONED")
    cur.execute("SELECT * FROM admission WHERE admission_id = %s FOR UPDATE", (admission_id,))
    row = cur.fetchone()
    if not row or row["status"] in ("RELEASED", "ABANDONED"):
        return False
    before = active_admission_count(cur, row["lab_id"])
    cur.execute(
        """
        UPDATE admission
        SET status = %s, released_at = NOW(3), reason = %s
        WHERE admission_id = %s
        """,
        (status, reason[:500], admission_id),
    )
    after = active_admission_count(cur, row["lab_id"])
    cur.execute(
        """
        INSERT INTO lab_quota_log
          (lab_id, round_id, change_type, delta, active_count_before,
           active_count_after, admission_id, operator_id, remark)
        VALUES (%s, %s, 'RELEASE', -1, %s, %s, %s, %s, %s)
        """,
        (row["lab_id"], row["round_id"], before, after, admission_id, operator_id, reason[:255]),
    )
    return True


def release_expired_admissions(limit: int = 200) -> dict:
    """扫描确认超时的拟录取并释放名额（RELEASED），写通知与留痕。"""
    conn = get_conn()
    released_ids = []
    try:
        with conn.cursor() as cur:
            cur.execute(
                """
                SELECT admission_id, student_id, lab_id
                FROM admission
                WHERE status = 'OCCUPIED'
                  AND confirm_deadline IS NOT NULL
                  AND confirm_deadline < NOW(3)
                ORDER BY admission_id
                LIMIT %s
                FOR UPDATE SKIP LOCKED
                """,
                (limit,),
            )
            rows = cur.fetchall()
            released_students = set()
            for row in rows:
                if release_admission(cur, row["admission_id"], 0, "确认超时，系统自动释放"):
                    notify_student(
                        cur,
                        row["student_id"],
                        "拟录取已超时释放",
                        "你的拟录取确认已超过截止时间，名额已自动释放，可参加后续补录或调剂。",
                        "ADMISSION",
                        row["admission_id"],
                    )
                    write_audit(
                        cur,
                        0,
                        "SYSTEM",
                        "ADMISSION_RELEASE_EXPIRED",
                        "ADMISSION",
                        row["admission_id"],
                        None,
                        {"student_id": row["student_id"], "lab_id": row["lab_id"]},
                    )
                    released_ids.append(row["admission_id"])
                    released_students.add(row["student_id"])

            # 释放后已无任何活跃录取的学生进入补录
            supplement_students = []
            for student_id in released_students:
                cur.execute(
                    """
                    SELECT 1 FROM admission
                    WHERE student_id = %s
                      AND status IN ('OCCUPIED','CONFIRMED','EFFECTIVE','ENROLLED')
                    LIMIT 1
                    """,
                    (student_id,),
                )
                if cur.fetchone():
                    continue
                cur.execute(
                    "UPDATE student SET selection_state = 'SUPPLEMENT' WHERE student_id = %s",
                    (student_id,),
                )
                notify_student(
                    cur,
                    student_id,
                    "已进入补录",
                    "你未在规定时间内确认拟录取，相关名额已全部释放，你已进入补录，请关注补录轮次开放。",
                    "STUDENT",
                    student_id,
                )
                write_audit(
                    cur,
                    0,
                    "SYSTEM",
                    "STUDENT_ENTER_SUPPLEMENT",
                    "STUDENT",
                    student_id,
                    None,
                    {"reason": "CONFIRM_EXPIRED"},
                )
                supplement_students.append(student_id)

            conn.commit()
            return {
                "released": len(released_ids),
                "admissionIds": released_ids,
                "supplementStudents": supplement_students,
            }
    except Exception:
        conn.rollback()
        raise
    finally:
        conn.close()


def notify_student(cur, student_id: int, title: str, content: str,
                   biz_type: str, biz_id: int) -> None:
    cur.execute("SELECT account_id FROM student WHERE student_id = %s", (student_id,))
    row = cur.fetchone()
    if not row:
        return
    cur.execute(
        """
        INSERT INTO notification
          (receiver_account_id, notice_type, title, content, biz_type, biz_id, sent_at)
        VALUES (%s, 'SELECTION_RESULT', %s, %s, %s, %s, NOW(3))
        """,
        (row["account_id"], title, content, biz_type, biz_id),
    )


logger = logging.getLogger("sxsxb.reminder")


def dispatch_due_reminders(limit: int = 100) -> dict:
    """扫描到期的 PENDING 提醒并发送：站内信落库，失败自动重试（最多 3 次）。"""
    conn = get_conn()
    stats = {"sent": 0, "failed": 0, "retried": 0}
    try:
        with conn.cursor() as cur:
            cur.execute(
                """
                SELECT r.reminder_id, r.student_id, r.channel, r.retry_count,
                       r.session_id, s.session_name, s.start_time, s.end_time,
                       s.location, l.name AS lab_name, a.account_id
                FROM interview_reminder r
                JOIN interview_session s ON s.session_id = r.session_id
                JOIN lab l ON l.lab_id = s.lab_id
                JOIN student st ON st.student_id = r.student_id
                JOIN account a ON a.account_id = st.account_id
                WHERE r.send_status = 'PENDING' AND r.remind_at <= NOW(3)
                ORDER BY r.remind_at
                LIMIT %s
                FOR UPDATE SKIP LOCKED
                """,
                (limit,),
            )
            due = cur.fetchall()

            for row in due:
                try:
                    if row["channel"] != "SITE":
                        raise RuntimeError(f"未配置渠道网关：{row['channel']}")
                    start_text = row["start_time"].strftime("%Y-%m-%d %H:%M")
                    end_text = row["end_time"].strftime("%H:%M")
                    content = (
                        f"请按时到{row['lab_name']}参加面试："
                        f"{row['session_name'] or '面试场次'}，"
                        f"时间 {start_text}–{end_text}，"
                        f"地点：{row['location'] or '以现场通知为准'}。"
                        "请提前 10 分钟到场并携带学生证。"
                    )
                    cur.execute(
                        """
                        INSERT INTO notification
                          (receiver_account_id, notice_type, title, content,
                           biz_type, biz_id, sent_at)
                        VALUES (%s, 'INTERVIEW_REMIND', '面试提醒', %s,
                                'INTERVIEW_REMINDER', %s, NOW(3))
                        """,
                        (row["account_id"], content, row["reminder_id"]),
                    )
                    cur.execute(
                        """
                        UPDATE interview_reminder
                        SET send_status = 'SENT', sent_at = NOW(3), fail_reason = NULL
                        WHERE reminder_id = %s
                        """,
                        (row["reminder_id"],),
                    )
                    stats["sent"] += 1
                except Exception as exc:
                    retry_count = row["retry_count"] + 1
                    reason = str(exc)[:255]
                    if retry_count < 3:
                        cur.execute(
                            """
                            UPDATE interview_reminder
                            SET retry_count = %s, fail_reason = %s, send_status = 'PENDING',
                                remind_at = DATE_ADD(NOW(3), INTERVAL 5 MINUTE)
                            WHERE reminder_id = %s
                            """,
                            (retry_count, reason, row["reminder_id"]),
                        )
                        stats["retried"] += 1
                    else:
                        cur.execute(
                            """
                            UPDATE interview_reminder
                            SET retry_count = %s, fail_reason = %s, send_status = 'FAILED'
                            WHERE reminder_id = %s
                            """,
                            (retry_count, reason, row["reminder_id"]),
                        )
                        stats["failed"] += 1
        conn.commit()
        return stats
    except Exception:
        conn.rollback()
        raise
    finally:
        conn.close()


async def _reminder_dispatch_loop() -> None:
    while True:
        try:
            await asyncio.to_thread(dispatch_due_reminders)
        except Exception:
            logger.exception("面试提醒派发失败")
        await asyncio.sleep(REMINDER_POLL_SECONDS)


async def _admission_expiry_loop() -> None:
    while True:
        try:
            stats = await asyncio.to_thread(release_expired_admissions)
            if stats["released"]:
                logger.info("确认超时释放 %s 条拟录取: %s", stats["released"], stats["admissionIds"])
        except Exception:
            logger.exception("拟录取超时释放失败")
        await asyncio.sleep(ADMISSION_EXPIRY_POLL_SECONDS)


@app.on_event("startup")
async def _start_reminder_dispatch() -> None:
    app.state.reminder_task = asyncio.create_task(_reminder_dispatch_loop())
    if os.getenv("AUTO_RELEASE_EXPIRED", "1") == "1":
        app.state.admission_expiry_task = asyncio.create_task(_admission_expiry_loop())


@app.on_event("shutdown")
async def _stop_reminder_dispatch() -> None:
    for name in ("reminder_task", "admission_expiry_task"):
        task = getattr(app.state, name, None)
        if task:
            task.cancel()


class LoginIn(BaseModel):
    loginName: str
    password: str


class RegisterIn(BaseModel):
    studentNo: str
    name: str
    password: str
    grade: Optional[str] = None
    major: Optional[str] = None
    phone: Optional[str] = None
    gradeRank: str
    awards: Optional[str] = None
    specialty: Optional[str] = None
    developmentDirection: Optional[str] = None


class IntentsIn(BaseModel):
    mentorIds: List[int]
    acceptInternalAdjust: bool = False
    acceptCrossAdjust: bool = False


class SessionIn(BaseModel):
    roundId: int
    labId: int
    sessionName: Optional[str] = None
    startTime: str
    endTime: str
    location: Optional[str] = None
    judgeTeacherIds: List[int] = []


class ParticipantIn(BaseModel):
    intentIds: Optional[List[int]] = None
    auto: bool = True


class CheckinIn(BaseModel):
    studentId: int
    intentId: Optional[int] = None
    status: str


class ScoreIn(BaseModel):
    studentId: int
    intentId: Optional[int] = None
    learningScore: float
    logicScore: float
    programmingScore: float
    timeScore: float
    comment: Optional[str] = None


class ProposeIn(BaseModel):
    intentId: int
    remark: Optional[str] = None


class AdjustMatchIn(BaseModel):
    roundId: int


class NegotiateIn(BaseModel):
    method: str = "FACE_TO_FACE"
    willingness: Optional[str] = None
    conclusion: str
    finalLabId: Optional[int] = None
    finalMentorId: Optional[int] = None
    remark: Optional[str] = None


class AbandonIn(BaseModel):
    reason: Optional[str] = None


class ReservationIn(BaseModel):
    labId: int
    visitDate: str
    timeSlot: str
    remark: Optional[str] = None


class PublicityBatchIn(BaseModel):
    roundId: Optional[int] = None
    startTime: str
    endTime: str
    remark: Optional[str] = None


class ObjectionIn(BaseModel):
    batchId: int
    content: str
    publicityItemId: Optional[int] = None


class ObjectionHandleIn(BaseModel):
    status: str
    resolution: Optional[str] = None


def now_of(cur) -> str:
    cur.execute("SELECT DATE_FORMAT(NOW(3), '%%Y-%%m-%%d %%H:%%i:%%s.%%f') AS now", ())
    return cur.fetchone()["now"]


def open_round(cur) -> Optional[dict]:
    """以负责人手动开启（status=FILLING）为准，时间窗仅作兜底，便于自由推进阶段。"""
    cur.execute(
        """
        SELECT round_id, round_no, round_type, round_name, intent_limit,
               fill_start, fill_end
        FROM selection_round
        WHERE status = 'FILLING'
        ORDER BY round_no LIMIT 1
        """
    )
    row = cur.fetchone()
    if row:
        return row
    cur.execute(
        """
        SELECT round_id, round_no, round_type, round_name, intent_limit,
               fill_start, fill_end
        FROM selection_round
        WHERE NOW(3) BETWEEN fill_start AND fill_end AND status <> 'CLOSED'
        ORDER BY round_no LIMIT 1
        """
    )
    return cur.fetchone()


def next_round(cur) -> Optional[dict]:
    cur.execute(
        """
        SELECT round_id, round_no, round_type, round_name, intent_limit,
               fill_start, fill_end
        FROM selection_round
        WHERE fill_start > NOW(3)
        ORDER BY fill_start LIMIT 1
        """
    )
    return cur.fetchone()


def ensure_fill_window(cur, round_id: int) -> dict:
    cur.execute(
        """
        SELECT round_id, round_no, round_name, round_type, intent_limit, fill_start, fill_end,
               status
        FROM selection_round WHERE round_id = %s
        """,
        (round_id,),
    )
    row = cur.fetchone()
    if not row:
        raise HTTPException(status_code=404, detail="轮次不存在")
    if row["status"] != "FILLING":
        raise HTTPException(status_code=409, detail="该轮次未开启填报")
    return row


def application_detail(cur, application_id: int, student_id: int) -> dict:
    cur.execute(
        """
        SELECT application_id, round_id, student_id, accept_internal_adjust,
               accept_cross_adjust, intent_count, status, submitted_at
        FROM intent_application
        WHERE application_id = %s AND student_id = %s
        """,
        (application_id, student_id),
    )
    app_row = cur.fetchone()
    if not app_row:
        raise HTTPException(status_code=404, detail="申请单不存在")
    cur.execute(
        """
        SELECT i.intent_id, i.parallel_no, i.mentor_id, i.lab_id, i.status,
               t.name AS mentor_name, l.name AS lab_name
        FROM intent i
        JOIN mentor m ON m.mentor_id = i.mentor_id
        JOIN teacher t ON t.teacher_id = m.teacher_id
        JOIN lab l ON l.lab_id = i.lab_id
        WHERE i.application_id = %s
        ORDER BY i.parallel_no
        """,
        (application_id,),
    )
    app_row["intents"] = cur.fetchall()
    return app_row


@app.get("/api/health")
def health():
    conn = get_conn()
    try:
        with conn.cursor() as cur:
            cur.execute("SELECT 1 AS ok")
            return {"ok": cur.fetchone()["ok"] == 1}
    finally:
        conn.close()


@app.post("/api/auth/login")
def login(body: LoginIn):
    conn = get_conn()
    try:
        with conn.cursor() as cur:
            cur.execute(
                """
                SELECT a.account_id, a.user_type, a.password_hash, a.status,
                       s.student_id, s.student_no, s.name AS student_name,
                       t.teacher_id, t.teacher_no, t.name AS teacher_name
                FROM account a
                LEFT JOIN student s ON s.account_id = a.account_id
                LEFT JOIN teacher t ON t.account_id = a.account_id
                WHERE a.login_name = %s
                """,
                (body.loginName,),
            )
            row = cur.fetchone()
            if not row or row["status"] != "ACTIVE":
                raise HTTPException(status_code=401, detail="账号或密码错误")
            expected = hashlib.sha256(body.password.encode("utf-8")).hexdigest()
            if not hmac.compare_digest(expected, row["password_hash"]):
                raise HTTPException(status_code=401, detail="账号或密码错误")
            if row["user_type"] == "STUDENT" and row["student_id"]:
                user = {
                    "userType": "STUDENT",
                    "studentId": row["student_id"],
                    "studentNo": row["student_no"],
                    "name": row["student_name"],
                }
            elif row["user_type"] == "TEACHER" and row["teacher_id"]:
                roles = teacher_roles(cur, row["teacher_id"])
                user = {
                    "userType": "TEACHER",
                    "teacherId": row["teacher_id"],
                    "teacherNo": row["teacher_no"],
                    "name": row["teacher_name"],
                    "roles": [
                        {"roleCode": r["role_code"], "scopeLabId": r["scope_lab_id"]}
                        for r in roles
                    ],
                }
            else:
                raise HTTPException(status_code=403, detail="账号类型不支持")
        return {"token": issue_token(row["account_id"]), "user": user}
    finally:
        conn.close()


@app.post("/api/auth/register")
def register(body: RegisterIn):
    """学生自助注册：以学号为登录名，注册成功后直接返回登录态。"""
    student_no = body.studentNo.strip()
    name = body.name.strip()
    if not re.fullmatch(r"[A-Za-z0-9]{4,32}", student_no):
        raise HTTPException(status_code=400, detail="学号需为 4-32 位字母或数字")
    if not name or len(name) > 50:
        raise HTTPException(status_code=400, detail="请填写真实姓名（不超过 50 字）")
    if len(body.password) < 6:
        raise HTTPException(status_code=400, detail="密码至少 6 位")
    if body.phone and not re.fullmatch(r"\d{6,20}", body.phone.strip()):
        raise HTTPException(status_code=400, detail="手机号格式不正确")
    grade = (body.grade or "").strip()
    major = (body.major or "").strip()
    if not grade:
        raise HTTPException(status_code=400, detail="请填写年级")
    if not major:
        raise HTTPException(status_code=400, detail="请填写专业")
    grade_rank = (body.gradeRank or "").strip()
    if not grade_rank:
        raise HTTPException(status_code=400, detail="请填写年级排名")
    if not re.search(r"\d", grade_rank):
        raise HTTPException(status_code=400, detail="年级排名需包含名次数字，如 3 或 3/120")

    conn = get_conn()
    try:
        with conn.cursor() as cur:
            cur.execute(
                """
                SELECT 1 FROM account WHERE login_name = %s OR user_no = %s
                UNION ALL
                SELECT 1 FROM student WHERE student_no = %s
                LIMIT 1
                """,
                (student_no, student_no, student_no),
            )
            if cur.fetchone():
                raise HTTPException(status_code=409, detail="该学号已注册，请直接登录")
            cur.execute(
                """
                INSERT INTO account (user_no, user_type, login_name, password_hash, status)
                VALUES (%s, 'STUDENT', %s, %s, 'ACTIVE')
                """,
                (student_no, student_no,
                 hashlib.sha256(body.password.encode("utf-8")).hexdigest()),
            )
            account_id = cur.lastrowid
            cur.execute(
                """
                INSERT INTO student
                  (account_id, student_no, name, grade, major, phone,
                   grade_rank, awards, specialty, dev_direction, selection_state)
                VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s, %s, 'NOT_FILLED')
                """,
                (account_id, student_no, name,
                 grade, major,
                 (body.phone or "").strip() or None,
                 grade_rank,
                 (body.awards or "").strip()[:500] or None,
                 (body.specialty or "").strip()[:200] or None,
                 (body.developmentDirection or "").strip()[:200] or None),
            )
            student_id = cur.lastrowid
            write_audit(
                cur, account_id, "STUDENT", "ACCOUNT_REGISTER", "ACCOUNT", account_id,
                None, {"student_no": student_no, "name": name},
            )
        conn.commit()
        user = {
            "userType": "STUDENT",
            "studentId": student_id,
            "studentNo": student_no,
            "name": name,
        }
        return {"token": issue_token(account_id), "user": user}
    except pymysql.err.IntegrityError:
        conn.rollback()
        raise HTTPException(status_code=409, detail="该学号已注册，请直接登录")
    except HTTPException:
        conn.rollback()
        raise
    finally:
        conn.close()


@app.get("/api/filing/context")
def filing_context(user: dict = Depends(current_student)):
    conn = get_conn()
    try:
        with conn.cursor() as cur:
            server_time = now_of(cur)
            cur_round = open_round(cur)
            upcoming = None if cur_round else next_round(cur)
            cur.execute(
                """
                SELECT stage_no, stage_code, stage_name, start_time, end_time, status
                FROM selection_stage WHERE stage_code = 'INTENT'
                """
            )
            stage = cur.fetchone()
            cur.execute(
                """
                SELECT m.mentor_id, m.capacity, m.research_direction,
                       t.name AS mentor_name, l.lab_id, l.lab_code, l.name AS lab_name,
                       l.quota, l.direction AS lab_direction
                FROM mentor m
                JOIN teacher t ON t.teacher_id = m.teacher_id
                JOIN lab l ON l.lab_id = m.lab_id
                WHERE m.status = 'ACTIVE' AND l.status = 'OPEN'
                ORDER BY l.lab_id, m.mentor_id
                """
            )
            mentors = cur.fetchall()
            application = None
            if cur_round:
                cur.execute(
                    """
                    SELECT application_id, round_id, status, intent_count
                    FROM intent_application
                    WHERE student_id = %s AND round_id = %s
                    """,
                    (user["student_id"], cur_round["round_id"]),
                )
                application = cur.fetchone()
        return {
            "serverTime": server_time,
            "stage": stage,
            "round": cur_round,
            "nextRound": upcoming,
            "isOpen": cur_round is not None,
            "intentLimit": cur_round["intent_limit"] if cur_round else None,
            "mentors": mentors,
            "application": application,
        }
    finally:
        conn.close()


@app.post("/api/filing/applications")
def create_application(user: dict = Depends(current_student)):
    conn = get_conn()
    try:
        with conn.cursor() as cur:
            round_row = open_round(cur)
            if not round_row:
                raise HTTPException(status_code=409, detail="当前不在意向填报时间窗内")
            ensure_supplement_eligible(cur, user["student_id"], round_row["round_type"])
            cur.execute(
                """
                SELECT application_id, status FROM intent_application
                WHERE student_id = %s AND round_id = %s
                FOR UPDATE
                """,
                (user["student_id"], round_row["round_id"]),
            )
            existing = cur.fetchone()
            if existing:
                if existing["status"] == "SUBMITTED":
                    raise HTTPException(status_code=409, detail="本轮意向已提交，不可重复建单")
                conn.commit()
                return {"applicationId": existing["application_id"], "status": "DRAFT", "created": False}
            cur.execute(
                """
                INSERT INTO intent_application
                  (round_id, student_id, accept_internal_adjust, accept_cross_adjust, status)
                VALUES (%s, %s, 0, 0, 'DRAFT')
                """,
                (round_row["round_id"], user["student_id"]),
            )
            application_id = cur.lastrowid
            conn.commit()
            return {"applicationId": application_id, "status": "DRAFT", "created": True}
    except HTTPException:
        conn.rollback()
        raise
    except Exception:
        conn.rollback()
        raise
    finally:
        conn.close()


@app.put("/api/filing/applications/{application_id}/intents")
def save_intents(application_id: int, body: IntentsIn, user: dict = Depends(current_student)):
    conn = get_conn()
    try:
        with conn.cursor() as cur:
            cur.execute(
                """
                SELECT application_id, round_id, status, student_id
                FROM intent_application
                WHERE application_id = %s AND student_id = %s
                FOR UPDATE
                """,
                (application_id, user["student_id"]),
            )
            application = cur.fetchone()
            if not application:
                raise HTTPException(status_code=404, detail="申请单不存在")
            if application["status"] != "DRAFT":
                raise HTTPException(status_code=409, detail="已提交，不能修改意向")
            round_row = ensure_fill_window(cur, application["round_id"])
            ensure_supplement_eligible(cur, user["student_id"], round_row["round_type"])

            mentor_ids = list(body.mentorIds)
            if not mentor_ids:
                raise HTTPException(status_code=400, detail="至少填写 1 个意向")
            if len(mentor_ids) > round_row["intent_limit"]:
                raise HTTPException(
                    status_code=400,
                    detail=f"本轮最多填写 {round_row['intent_limit']} 个意向",
                )
            if len(set(mentor_ids)) != len(mentor_ids):
                raise HTTPException(status_code=400, detail="同一导师不可重复选择")

            placeholders = ",".join(["%s"] * len(mentor_ids))
            cur.execute(
                f"""
                SELECT m.mentor_id, m.lab_id, m.status AS mentor_status, l.status AS lab_status
                FROM mentor m JOIN lab l ON l.lab_id = m.lab_id
                WHERE m.mentor_id IN ({placeholders})
                """,
                tuple(mentor_ids),
            )
            found = {r["mentor_id"]: r for r in cur.fetchall()}
            missing = [mid for mid in mentor_ids if mid not in found]
            if missing:
                raise HTTPException(status_code=400, detail=f"导师不存在：{missing}")
            unavailable = [
                mid for mid in mentor_ids
                if found[mid]["mentor_status"] != "ACTIVE" or found[mid]["lab_status"] != "OPEN"
            ]
            if unavailable:
                raise HTTPException(status_code=409, detail=f"导师或实训室已停止接收：{unavailable}")

            cur.execute("DELETE FROM intent WHERE application_id = %s", (application_id,))
            rows = [
                (
                    application_id,
                    application["round_id"],
                    user["student_id"],
                    mid,
                    found[mid]["lab_id"],
                    idx + 1,
                )
                for idx, mid in enumerate(mentor_ids)
            ]
            cur.executemany(
                """
                INSERT INTO intent
                  (application_id, round_id, student_id, mentor_id, lab_id, parallel_no, status)
                VALUES (%s, %s, %s, %s, %s, %s, 'DRAFT')
                """,
                rows,
            )
            cur.execute(
                """
                UPDATE intent_application
                SET intent_count = %s, accept_internal_adjust = %s, accept_cross_adjust = %s
                WHERE application_id = %s
                """,
                (len(rows), int(body.acceptInternalAdjust), int(body.acceptCrossAdjust), application_id),
            )
            conn.commit()
            return application_detail(cur, application_id, user["student_id"])
    except HTTPException:
        conn.rollback()
        raise
    except Exception:
        conn.rollback()
        raise
    finally:
        conn.close()


@app.post("/api/filing/applications/{application_id}/submit")
def submit_application(application_id: int, user: dict = Depends(current_student)):
    conn = get_conn()
    try:
        with conn.cursor() as cur:
            cur.execute(
                """
                SELECT application_id, round_id, status, intent_count,
                       accept_internal_adjust, accept_cross_adjust
                FROM intent_application
                WHERE application_id = %s AND student_id = %s
                FOR UPDATE
                """,
                (application_id, user["student_id"]),
            )
            application = cur.fetchone()
            if not application:
                raise HTTPException(status_code=404, detail="申请单不存在")
            if application["status"] != "DRAFT":
                raise HTTPException(status_code=409, detail="该申请单已提交")
            ensure_fill_window(cur, application["round_id"])
            cur.execute("SELECT COUNT(*) AS c FROM intent WHERE application_id = %s", (application_id,))
            count = cur.fetchone()["c"]
            if count == 0:
                raise HTTPException(status_code=400, detail="请先填写至少 1 个意向")

            cur.execute(
                """
                UPDATE intent_application
                SET status = 'SUBMITTED', intent_count = %s, submitted_at = NOW(3)
                WHERE application_id = %s
                """,
                (count, application_id),
            )
            cur.execute(
                """
                UPDATE intent SET status = 'SUBMITTED', submitted_at = NOW(3)
                WHERE application_id = %s
                """,
                (application_id,),
            )
            cur.execute(
                "UPDATE student SET selection_state = 'FILLED' WHERE student_id = %s",
                (user["student_id"],),
            )
            cur.execute(
                """
                SELECT intent_id, parallel_no, mentor_id, lab_id
                FROM intent WHERE application_id = %s ORDER BY parallel_no
                """,
                (application_id,),
            )
            intents_snapshot = cur.fetchall()
            write_audit(
                cur,
                user["account_id"],
                "STUDENT",
                "APPLICATION_SUBMIT",
                "INTENT_APPLICATION",
                application_id,
                {
                    "status": application["status"],
                    "intent_count": application["intent_count"],
                    "accept_internal_adjust": application["accept_internal_adjust"],
                    "accept_cross_adjust": application["accept_cross_adjust"],
                },
                {
                    "status": "SUBMITTED",
                    "intent_count": count,
                    "accept_internal_adjust": application["accept_internal_adjust"],
                    "accept_cross_adjust": application["accept_cross_adjust"],
                    "intents": [
                        {
                            "intent_id": i["intent_id"],
                            "parallel_no": i["parallel_no"],
                            "mentor_id": i["mentor_id"],
                            "lab_id": i["lab_id"],
                        }
                        for i in intents_snapshot
                    ],
                },
            )
            conn.commit()
            return application_detail(cur, application_id, user["student_id"])
    except HTTPException:
        conn.rollback()
        raise
    except Exception:
        conn.rollback()
        raise
    finally:
        conn.close()


@app.get("/api/filing/applications/{application_id}")
def get_application(application_id: int, user: dict = Depends(current_student)):
    conn = get_conn()
    try:
        with conn.cursor() as cur:
            return application_detail(cur, application_id, user["student_id"])
    finally:
        conn.close()


@app.post("/api/interview/sessions")
def create_interview_session(body: SessionIn, user: dict = Depends(current_teacher)):
    conn = get_conn()
    try:
        with conn.cursor() as cur:
            require_lab_manager(cur, user, body.labId)
            cur.execute("SELECT lab_id, name, status FROM lab WHERE lab_id = %s", (body.labId,))
            lab = cur.fetchone()
            if not lab:
                raise HTTPException(status_code=404, detail="实训室不存在")
            if lab["status"] != "OPEN":
                raise HTTPException(status_code=409, detail="实训室未开放")
            cur.execute(
                """
                SELECT round_id, round_type, round_name, interview_start, interview_end
                FROM selection_round WHERE round_id = %s
                """,
                (body.roundId,),
            )
            round_row = cur.fetchone()
            if not round_row:
                raise HTTPException(status_code=404, detail="轮次不存在")
            if round_row["round_type"] == "ADJUST":
                raise HTTPException(status_code=400, detail="统筹调剂轮次不安排面试")
            start = parse_dt(body.startTime)
            end = parse_dt(body.endTime)
            if start >= end:
                raise HTTPException(status_code=400, detail="面试结束时间必须晚于开始时间")
            # 评委不强制指定，可在后续安排中灵活调整
            if body.judgeTeacherIds:
                placeholders = ",".join(["%s"] * len(body.judgeTeacherIds))
                cur.execute(
                    f"""
                    SELECT teacher_id FROM teacher_role
                    WHERE role_code = 'MENTOR' AND status = 'ACTIVE'
                      AND scope_lab_id = %s AND teacher_id IN ({placeholders})
                    """,
                    tuple([body.labId] + list(body.judgeTeacherIds)),
                )
                valid_judges = {r["teacher_id"] for r in cur.fetchall()}
                invalid = [tid for tid in body.judgeTeacherIds if tid not in valid_judges]
                if invalid:
                    raise HTTPException(status_code=400, detail=f"以下老师不是该实训室导师：{invalid}")

            cur.execute(
                """
                INSERT INTO interview_session
                  (round_id, lab_id, session_name, start_time, end_time, location, organizer_teacher_id)
                VALUES (%s, %s, %s, %s, %s, %s, %s)
                """,
                (
                    body.roundId,
                    body.labId,
                    body.sessionName,
                    start,
                    end,
                    body.location,
                    user["teacher_id"],
                ),
            )
            session_id = cur.lastrowid
            if body.judgeTeacherIds:
                cur.executemany(
                    """
                    INSERT INTO interview_judge (session_id, teacher_id, judge_role)
                    VALUES (%s, %s, 'JUDGE')
                    """,
                    [(session_id, tid) for tid in body.judgeTeacherIds],
                )
            write_audit(
                cur,
                user["account_id"],
                "TEACHER",
                "INTERVIEW_SESSION_CREATE",
                "INTERVIEW_SESSION",
                session_id,
                None,
                {
                    "round_id": body.roundId,
                    "lab_id": body.labId,
                    "start_time": start.isoformat(sep=" "),
                    "end_time": end.isoformat(sep=" "),
                    "judge_teacher_ids": body.judgeTeacherIds,
                },
            )
            conn.commit()
            return {
                "sessionId": session_id,
                "roundId": body.roundId,
                "labId": body.labId,
                "labName": lab["name"],
                "startTime": start,
                "endTime": end,
                "location": body.location,
                "judges": body.judgeTeacherIds,
            }
    except HTTPException:
        conn.rollback()
        raise
    except Exception:
        conn.rollback()
        raise
    finally:
        conn.close()


@app.post("/api/interview/sessions/{session_id}/participants")
def assign_interview_participants(session_id: int, body: ParticipantIn,
                                  user: dict = Depends(current_teacher)):
    conn = get_conn()
    try:
        with conn.cursor() as cur:
            cur.execute(
                """
                SELECT session_id, round_id, lab_id, session_name,
                       start_time, end_time, location, status
                FROM interview_session WHERE session_id = %s FOR UPDATE
                """,
                (session_id,),
            )
            session = cur.fetchone()
            if not session:
                raise HTTPException(status_code=404, detail="面试场次不存在")
            require_lab_manager(cur, user, session["lab_id"])
            if session["status"] != "PLANNED":
                raise HTTPException(status_code=409, detail="仅 PLANNED 状态的场次可排期")

            if body.auto:
                cur.execute(
                    """
                    SELECT i.intent_id, i.student_id
                    FROM intent i
                    WHERE i.round_id = %s AND i.lab_id = %s AND i.status = 'SUBMITTED'
                      AND NOT EXISTS (
                        SELECT 1 FROM interview_participant p
                        WHERE p.intent_id = i.intent_id
                      )
                    ORDER BY i.student_id, i.parallel_no
                    """,
                    (session["round_id"], session["lab_id"]),
                )
                candidates = cur.fetchall()
                skipped = []
            else:
                intent_ids = body.intentIds or []
                if not intent_ids:
                    raise HTTPException(status_code=400, detail="intentIds 不能为空")
                placeholders = ",".join(["%s"] * len(intent_ids))
                cur.execute(
                    f"""
                    SELECT intent_id, student_id, status
                    FROM intent
                    WHERE intent_id IN ({placeholders})
                      AND round_id = %s AND lab_id = %s
                    """,
                    tuple(intent_ids + [session["round_id"], session["lab_id"]]),
                )
                rows = cur.fetchall()
                found = {r["intent_id"]: r for r in rows}
                missing = [iid for iid in intent_ids if iid not in found]
                if missing:
                    raise HTTPException(status_code=400, detail=f"意向不存在或不属于本场次：{missing}")
                not_submitted = [iid for iid in intent_ids if found[iid]["status"] != "SUBMITTED"]
                if not_submitted:
                    raise HTTPException(status_code=409, detail=f"以下意向尚未提交：{not_submitted}")
                cur.execute(
                    "SELECT intent_id FROM interview_participant WHERE session_id = %s",
                    (session_id,),
                )
                assigned_intents = {r["intent_id"] for r in cur.fetchall()}
                candidates = []
                skipped = []
                for iid in intent_ids:
                    row = found[iid]
                    if iid in assigned_intents:
                        skipped.append(iid)
                    else:
                        candidates.append(row)

            added = 0
            reminders = 0
            notified = 0
            notified_students = set()
            start_text = session["start_time"].strftime("%Y-%m-%d %H:%M")
            end_text = session["end_time"].strftime("%H:%M")
            for row in candidates:
                cur.execute(
                    """
                    INSERT INTO interview_participant (session_id, intent_id, student_id)
                    VALUES (%s, %s, %s)
                    """,
                    (session_id, row["intent_id"], row["student_id"]),
                )
                participant_id = cur.lastrowid
                added += 1
                # 默认由学生所选导师担任评委
                cur.execute(
                    """
                    SELECT m.teacher_id FROM intent i
                    JOIN mentor m ON m.mentor_id = i.mentor_id
                    WHERE i.intent_id = %s
                    """,
                    (row["intent_id"],),
                )
                mentor_row = cur.fetchone()
                if mentor_row:
                    cur.execute(
                        """
                        INSERT IGNORE INTO interview_judge (session_id, teacher_id, judge_role)
                        VALUES (%s, %s, 'MENTOR')
                        """,
                        (session_id, mentor_row["teacher_id"]),
                    )
                for offset in (1440, 120):
                    remind_at = session["start_time"] - timedelta(minutes=offset)
                    if remind_at <= datetime.now():
                        continue
                    cur.execute(
                        """
                        INSERT IGNORE INTO interview_reminder
                          (session_id, intent_id, participant_id, student_id,
                           offset_minutes, remind_at, channel, send_status)
                        VALUES (%s, %s, %s, %s, %s, %s, 'SITE', 'PENDING')
                        """,
                        (
                            session_id,
                            row["intent_id"],
                            participant_id,
                            row["student_id"],
                            offset,
                            remind_at,
                        ),
                    )
                    reminders += 1
                # 排入即向学生发出面试提醒（同一学生同场次多个意向只发一条）
                if row["student_id"] not in notified_students:
                    cur.execute(
                        "SELECT account_id FROM student WHERE student_id = %s",
                        (row["student_id"],),
                    )
                    stu = cur.fetchone()
                    if stu:
                        content = (
                            f"你已被安排参加“{session['session_name'] or '面试场次'}”面试，"
                            f"时间 {start_text}–{end_text}，"
                            f"地点：{session['location'] or '以现场通知为准'}。"
                            "请提前 10 分钟到场并携带学生证签到。"
                        )
                        cur.execute(
                            """
                            INSERT INTO notification
                              (receiver_account_id, notice_type, title, content,
                               biz_type, biz_id, sent_at)
                            VALUES (%s, 'INTERVIEW', '面试安排提醒', %s,
                                    'INTERVIEW_SESSION', %s, NOW(3))
                            """,
                            (stu["account_id"], content, session_id),
                        )
                        notified_students.add(row["student_id"])
                        notified += 1

            write_audit(
                cur,
                user["account_id"],
                "TEACHER",
                "INTERVIEW_ASSIGN",
                "INTERVIEW_SESSION",
                session_id,
                None,
                {
                    "session_id": session_id,
                    "added_intent_ids": [row["intent_id"] for row in candidates],
                    "skipped_intent_ids": skipped,
                    "reminders": reminders,
                    "notified": notified,
                },
            )
            conn.commit()
            return {
                "sessionId": session_id,
                "added": added,
                "skipped": skipped,
                "reminders": reminders,
                "notified": notified,
            }
    except HTTPException:
        conn.rollback()
        raise
    except Exception:
        conn.rollback()
        raise
    finally:
        conn.close()


@app.get("/api/interview/sessions")
def list_interview_sessions(roundId: Optional[int] = None,
                            labId: Optional[int] = None,
                            user: dict = Depends(current_teacher)):
    conn = get_conn()
    try:
        with conn.cursor() as cur:
            roles = teacher_roles(cur, user["teacher_id"])
            is_coordinator = any(r["role_code"] == "COORDINATOR" for r in roles)
            owned_labs = sorted({r["scope_lab_id"] for r in roles if r["role_code"] == "LAB_OWNER"})
            mentor_labs = sorted({
                r["scope_lab_id"] for r in roles
                if r["role_code"] == "MENTOR" and r["scope_lab_id"] is not None
            })
            allowed_labs = sorted(set(owned_labs) | set(mentor_labs))
            if labId is not None and not is_coordinator and labId not in allowed_labs:
                raise HTTPException(status_code=403, detail="无权查看该实训室的面试安排")
            if not is_coordinator and not allowed_labs:
                raise HTTPException(status_code=403, detail="当前账号无实训室管理权限")

            conditions = ["s.status <> 'CANCELED'"]
            params: list = []
            if roundId is not None:
                conditions.append("s.round_id = %s")
                params.append(roundId)
            if labId is not None:
                conditions.append("s.lab_id = %s")
                params.append(labId)
            if not is_coordinator:
                parts = []
                if owned_labs:
                    parts.append("s.lab_id IN (" + ",".join(["%s"] * len(owned_labs)) + ")")
                    params.extend(owned_labs)
                if mentor_labs:
                    parts.append(
                        "(s.lab_id IN (" + ",".join(["%s"] * len(mentor_labs)) + ") AND ("
                        "EXISTS (SELECT 1 FROM interview_judge j "
                        "WHERE j.session_id = s.session_id AND j.teacher_id = %s) "
                        "OR EXISTS (SELECT 1 FROM interview_participant p "
                        "JOIN intent i ON i.intent_id = p.intent_id "
                        "JOIN mentor m ON m.mentor_id = i.mentor_id "
                        "WHERE p.session_id = s.session_id AND m.teacher_id = %s)))"
                    )
                    params.extend(mentor_labs)
                    params.extend([user["teacher_id"], user["teacher_id"]])
                conditions.append("(" + " OR ".join(parts) + ")")
            where_sql = " AND ".join(conditions) if conditions else "1 = 1"
            cur.execute(
                f"""
                SELECT s.session_id, s.round_id, r.round_name, r.round_no,
                       s.lab_id, l.name AS lab_name,
                       s.session_name, s.start_time, s.end_time, s.location, s.status,
                       (SELECT COUNT(*) FROM interview_judge j
                        WHERE j.session_id = s.session_id) AS judge_count,
                       (SELECT COUNT(*) FROM interview_participant p
                        WHERE p.session_id = s.session_id) AS participant_count
                FROM interview_session s
                JOIN lab l ON l.lab_id = s.lab_id
                LEFT JOIN selection_round r ON r.round_id = s.round_id
                WHERE {where_sql}
                ORDER BY s.start_time, s.session_id
                """,
                tuple(params),
            )
            return cur.fetchall()
    finally:
        conn.close()


@app.get("/api/interview/sessions/{session_id}")
def get_interview_session(session_id: int, user: dict = Depends(current_teacher)):
    conn = get_conn()
    try:
        with conn.cursor() as cur:
            cur.execute(
                """
                SELECT s.session_id, s.round_id, s.lab_id, l.name AS lab_name,
                       s.session_name, s.start_time, s.end_time, s.location, s.status,
                       s.organizer_teacher_id
                FROM interview_session s JOIN lab l ON l.lab_id = s.lab_id
                WHERE s.session_id = %s
                """,
                (session_id,),
            )
            session = cur.fetchone()
            if not session:
                raise HTTPException(status_code=404, detail="面试场次不存在")
            roles = teacher_roles(cur, user["teacher_id"])
            is_manager = any(r["role_code"] == "COORDINATOR" for r in roles) or any(
                r["role_code"] == "LAB_OWNER" and r["scope_lab_id"] == session["lab_id"]
                for r in roles
            )
            if not is_manager:
                cur.execute(
                    "SELECT 1 FROM interview_judge "
                    "WHERE session_id = %s AND teacher_id = %s",
                    (session_id, user["teacher_id"]),
                )
                allowed = cur.fetchone() is not None
                if not allowed:
                    cur.execute(
                        """
                        SELECT 1 FROM interview_participant p
                        JOIN intent i ON i.intent_id = p.intent_id
                        JOIN mentor m ON m.mentor_id = i.mentor_id
                        WHERE p.session_id = %s AND m.teacher_id = %s
                        LIMIT 1
                        """,
                        (session_id, user["teacher_id"]),
                    )
                    allowed = cur.fetchone() is not None
                if not allowed:
                    raise HTTPException(status_code=403, detail="无权查看该场次")
            cur.execute(
                """
                SELECT j.teacher_id, j.judge_role, t.name AS teacher_name, t.teacher_no
                FROM interview_judge j JOIN teacher t ON t.teacher_id = j.teacher_id
                WHERE j.session_id = %s
                """,
                (session_id,),
            )
            session["judges"] = cur.fetchall()
            cur.execute(
                """
                SELECT p.participant_id, p.student_id, st.student_no, st.name AS student_name,
                       p.intent_id, p.checkin_status, i.parallel_no,
                       t.name AS mentor_name
                FROM interview_participant p
                JOIN student st ON st.student_id = p.student_id
                JOIN intent i ON i.intent_id = p.intent_id
                JOIN mentor m ON m.mentor_id = i.mentor_id
                JOIN teacher t ON t.teacher_id = m.teacher_id
                WHERE p.session_id = %s AND (%s OR m.teacher_id = %s)
                ORDER BY p.participant_id
                """,
                (session_id, is_manager, user["teacher_id"]),
            )
            session["participants"] = cur.fetchall()
            cur.execute(
                """
                SELECT COUNT(*) AS reminder_count, MIN(remind_at) AS first_remind_at
                FROM interview_reminder WHERE session_id = %s
                """,
                (session_id,),
            )
            session["reminderSummary"] = cur.fetchone()
            return session
    finally:
        conn.close()


@app.get("/api/interview/sessions/{session_id}/students/{student_id}/profile")
def get_session_student_profile(session_id: int, student_id: int,
                                user: dict = Depends(current_teacher)):
    """评分时查看本场次学生的注册简历（需具备该场次查看权限）。"""
    conn = get_conn()
    try:
        with conn.cursor() as cur:
            cur.execute("SELECT lab_id FROM interview_session WHERE session_id = %s", (session_id,))
            session = cur.fetchone()
            if not session:
                raise HTTPException(status_code=404, detail="面试场次不存在")
            roles = teacher_roles(cur, user["teacher_id"])
            is_manager = any(r["role_code"] == "COORDINATOR" for r in roles) or any(
                r["role_code"] == "LAB_OWNER" and r["scope_lab_id"] == session["lab_id"]
                for r in roles
            )
            if not is_manager:
                cur.execute(
                    "SELECT 1 FROM interview_judge WHERE session_id = %s AND teacher_id = %s",
                    (session_id, user["teacher_id"]),
                )
                allowed = cur.fetchone() is not None
                if not allowed:
                    cur.execute(
                        """
                        SELECT 1 FROM interview_participant p
                        JOIN intent i ON i.intent_id = p.intent_id
                        JOIN mentor m ON m.mentor_id = i.mentor_id
                        WHERE p.session_id = %s AND m.teacher_id = %s
                        LIMIT 1
                        """,
                        (session_id, user["teacher_id"]),
                    )
                    allowed = cur.fetchone() is not None
                if not allowed:
                    raise HTTPException(status_code=403, detail="无权查看该场次")
            cur.execute(
                "SELECT 1 FROM interview_participant WHERE session_id = %s AND student_id = %s LIMIT 1",
                (session_id, student_id),
            )
            if not cur.fetchone():
                raise HTTPException(status_code=404, detail="该学生不在本场次名单中")
            cur.execute(
                """
                SELECT s.student_id, s.student_no, s.name, s.gender, s.grade, s.major, s.phone,
                       s.grade_rank, s.awards, s.specialty, s.dev_direction, s.selection_state
                FROM student s WHERE s.student_id = %s
                """,
                (student_id,),
            )
            profile = cur.fetchone()
            if not profile:
                raise HTTPException(status_code=404, detail="学生不存在")
            return profile
    finally:
        conn.close()


@app.post("/api/interview/sessions/{session_id}/cancel")
def cancel_interview_session(session_id: int, user: dict = Depends(current_teacher)):
    """撤销面试场次：清空排期、评委与提醒，场次保留为 CANCELED 便于留痕与重新排期。"""
    conn = get_conn()
    try:
        with conn.cursor() as cur:
            cur.execute(
                """
                SELECT session_id, round_id, lab_id, session_name, start_time, status
                FROM interview_session WHERE session_id = %s FOR UPDATE
                """,
                (session_id,),
            )
            session = cur.fetchone()
            if not session:
                raise HTTPException(status_code=404, detail="面试场次不存在")
            require_lab_manager(cur, user, session["lab_id"])
            if session["status"] == "CANCELED":
                raise HTTPException(status_code=409, detail="该场次已撤销")
            cur.execute(
                "SELECT COUNT(*) AS c FROM interview_score WHERE session_id = %s",
                (session_id,),
            )
            if cur.fetchone()["c"] > 0:
                raise HTTPException(status_code=409, detail="该场次已有评分记录，不能撤销")

            cur.execute(
                """
                SELECT DISTINCT p.student_id, a.account_id
                FROM interview_participant p
                JOIN student s ON s.student_id = p.student_id
                JOIN account a ON a.account_id = s.account_id
                WHERE p.session_id = %s
                """,
                (session_id,),
            )
            students = cur.fetchall()
            cur.execute("DELETE FROM interview_reminder WHERE session_id = %s", (session_id,))
            cur.execute("DELETE FROM interview_participant WHERE session_id = %s", (session_id,))
            cur.execute("DELETE FROM interview_judge WHERE session_id = %s", (session_id,))
            cur.execute(
                "UPDATE interview_session SET status = 'CANCELED' WHERE session_id = %s",
                (session_id,),
            )
            for row in students:
                cur.execute(
                    """
                    INSERT INTO notification
                      (receiver_account_id, notice_type, title, content, biz_type, biz_id, sent_at)
                    VALUES (%s, 'INTERVIEW', '面试场次已撤销', %s,
                            'INTERVIEW_SESSION', %s, NOW(3))
                    """,
                    (
                        row["account_id"],
                        f"原安排的面试场次“{session['session_name'] or '面试场次'}”"
                        f"（{session['start_time'].strftime('%Y-%m-%d %H:%M')}）已撤销，"
                        "请等待新的面试安排通知。",
                        session_id,
                    ),
                )
            write_audit(
                cur,
                user["account_id"],
                "TEACHER",
                "INTERVIEW_SESSION_CANCEL",
                "INTERVIEW_SESSION",
                session_id,
                {"status": session["status"], "participants": len(students)},
                {"status": "CANCELED", "removed_participants": len(students)},
            )
            conn.commit()
            return {
                "sessionId": session_id,
                "status": "CANCELED",
                "removedParticipants": len(students),
                "notified": len(students),
            }
    except HTTPException:
        conn.rollback()
        raise
    except Exception:
        conn.rollback()
        raise
    finally:
        conn.close()


@app.get("/api/interview/my-schedule")
def my_interview_schedule(user: dict = Depends(current_student)):
    conn = get_conn()
    try:
        with conn.cursor() as cur:
            cur.execute(
                """
                SELECT s.session_id, s.session_name, s.start_time, s.end_time,
                       s.location, s.status, l.name AS lab_name,
                       p.intent_id, p.checkin_status,
                       i.parallel_no, t.name AS mentor_name
                FROM interview_participant p
                JOIN interview_session s ON s.session_id = p.session_id
                JOIN lab l ON l.lab_id = s.lab_id
                JOIN intent i ON i.intent_id = p.intent_id
                JOIN mentor m ON m.mentor_id = i.mentor_id
                JOIN teacher t ON t.teacher_id = m.teacher_id
                WHERE p.student_id = %s
                ORDER BY s.start_time
                """,
                (user["student_id"],),
            )
            sessions = cur.fetchall()
            for item in sessions:
                cur.execute(
                    """
                    SELECT reminder_id, offset_minutes, remind_at, send_status, read_at, ack_at
                    FROM interview_reminder
                    WHERE session_id = %s AND student_id = %s
                    ORDER BY offset_minutes DESC
                    """,
                    (item["session_id"], user["student_id"]),
                )
                item["reminders"] = cur.fetchall()
            return sessions
    finally:
        conn.close()


@app.post("/api/interview/reminders/dispatch")
def trigger_reminder_dispatch(user: dict = Depends(current_teacher)):
    conn = get_conn()
    try:
        with conn.cursor() as cur:
            require_coordinator(cur, user)
    finally:
        conn.close()
    return dispatch_due_reminders()


@app.post("/api/interview/reminders/{reminder_id}/ack")
def ack_interview_reminder(reminder_id: int, user: dict = Depends(current_student)):
    conn = get_conn()
    try:
        with conn.cursor() as cur:
            cur.execute(
                """
                SELECT reminder_id, student_id, send_status, ack_at, read_at
                FROM interview_reminder WHERE reminder_id = %s FOR UPDATE
                """,
                (reminder_id,),
            )
            reminder = cur.fetchone()
            if not reminder:
                raise HTTPException(status_code=404, detail="提醒不存在")
            if reminder["student_id"] != user["student_id"]:
                raise HTTPException(status_code=403, detail="无权操作他人的面试提醒")
            if reminder["send_status"] != "SENT":
                raise HTTPException(status_code=409, detail="该提醒尚未发送，暂不能回执")
            cur.execute(
                """
                UPDATE interview_reminder
                SET ack_at = COALESCE(ack_at, NOW(3)),
                    read_at = COALESCE(read_at, NOW(3))
                WHERE reminder_id = %s
                """,
                (reminder_id,),
            )
            cur.execute(
                """
                UPDATE notification
                SET read_flag = 1, read_at = COALESCE(read_at, NOW(3))
                WHERE notice_type = 'INTERVIEW_REMIND'
                  AND biz_type = 'INTERVIEW_REMINDER'
                  AND biz_id = %s
                  AND receiver_account_id = %s
                """,
                (reminder_id, user["account_id"]),
            )
            cur.execute(
                "SELECT ack_at, read_at FROM interview_reminder WHERE reminder_id = %s",
                (reminder_id,),
            )
            result = cur.fetchone()
            conn.commit()
            return {
                "reminderId": reminder_id,
                "ackAt": result["ack_at"],
                "readAt": result["read_at"],
            }
    except HTTPException:
        conn.rollback()
        raise
    except Exception:
        conn.rollback()
        raise
    finally:
        conn.close()


@app.post("/api/interview/sessions/{session_id}/checkin")
def checkin_participant(session_id: int, body: CheckinIn,
                        user: dict = Depends(current_teacher)):
    checkin_status = body.status.upper()
    if checkin_status not in ("CHECKED_IN", "ABSENT", "NOT_CHECKED_IN"):
        raise HTTPException(status_code=400, detail="签到状态不合法")
    conn = get_conn()
    try:
        with conn.cursor() as cur:
            cur.execute(
                """
                SELECT session_id, lab_id, status FROM interview_session
                WHERE session_id = %s FOR UPDATE
                """,
                (session_id,),
            )
            session = cur.fetchone()
            if not session:
                raise HTTPException(status_code=404, detail="面试场次不存在")
            if session["status"] == "CANCELED":
                raise HTTPException(status_code=409, detail="场次已取消")
            participant = find_session_participant(cur, session_id, body.studentId, body.intentId)
            if not can_manage_interview(cur, user, session["lab_id"], participant["intent_id"]):
                raise HTTPException(status_code=403, detail="仅该学生所选导师（或本室负责人/统筹岗）可修改其签到")
            cur.execute(
                """
                UPDATE interview_participant
                SET checkin_status = %s,
                    checkin_at = CASE WHEN %s = 'NOT_CHECKED_IN' THEN NULL ELSE NOW(3) END
                WHERE participant_id = %s
                """,
                (checkin_status, checkin_status, participant["participant_id"]),
            )
            write_audit(
                cur,
                user["account_id"],
                "TEACHER",
                "INTERVIEW_CHECKIN",
                "INTERVIEW_PARTICIPANT",
                participant["participant_id"],
                None,
                {"session_id": session_id, "student_id": body.studentId, "status": checkin_status},
            )
            conn.commit()
            return {
                "participantId": participant["participant_id"],
                "studentId": body.studentId,
                "checkinStatus": checkin_status,
            }
    except HTTPException:
        conn.rollback()
        raise
    except Exception:
        conn.rollback()
        raise
    finally:
        conn.close()


@app.post("/api/interview/sessions/{session_id}/scores")
def submit_interview_score(session_id: int, body: ScoreIn,
                           user: dict = Depends(current_teacher)):
    conn = get_conn()
    try:
        with conn.cursor() as cur:
            cur.execute(
                """
                SELECT session_id, round_id, lab_id, start_time, status
                FROM interview_session WHERE session_id = %s
                """,
                (session_id,),
            )
            session = cur.fetchone()
            if not session:
                raise HTTPException(status_code=404, detail="面试场次不存在")
            if session["status"] == "CANCELED":
                raise HTTPException(status_code=409, detail="场次已取消")
            participant = find_session_participant(cur, session_id, body.studentId, body.intentId)
            if not can_manage_interview(cur, user, session["lab_id"], participant["intent_id"]):
                raise HTTPException(status_code=403, detail="仅该学生所选导师（或本室负责人/统筹岗）可评分")
            cur.execute(
                "SELECT round_type FROM selection_round WHERE round_id = %s",
                (session["round_id"],),
            )
            round_row = cur.fetchone()
            scheme = scheme_for_round(cur, round_row["round_type"])

            max_score = float(scheme["max_score"])
            dimensions = {
                "learningScore": body.learningScore,
                "logicScore": body.logicScore,
                "programmingScore": body.programmingScore,
                "timeScore": body.timeScore,
            }
            for name, value in dimensions.items():
                if value < 0 or value > max_score:
                    raise HTTPException(
                        status_code=400,
                        detail=f"{name} 必须在 0～{max_score:g} 之间",
                    )

            if participant["checkin_status"] != "CHECKED_IN":
                raise HTTPException(status_code=409, detail="学生未签到，不能评分")

            total = round(
                (
                    body.learningScore * float(scheme["learning_weight"])
                    + body.logicScore * float(scheme["logic_weight"])
                    + body.programmingScore * float(scheme["programming_weight"])
                    + body.timeScore * float(scheme["time_weight"])
                )
                / 100.0,
                2,
            )
            cur.execute(
                """
                INSERT INTO interview_score
                  (session_id, intent_id, student_id, judge_teacher_id,
                   learning_score, logic_score, programming_score, time_score,
                   total_score, comment, status, submitted_at)
                VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s, %s, 'SUBMITTED', NOW(3))
                ON DUPLICATE KEY UPDATE
                  learning_score = VALUES(learning_score),
                  logic_score = VALUES(logic_score),
                  programming_score = VALUES(programming_score),
                  time_score = VALUES(time_score),
                  total_score = VALUES(total_score),
                  comment = VALUES(comment),
                  status = 'SUBMITTED',
                  submitted_at = NOW(3)
                """,
                (
                    session_id,
                    participant["intent_id"],
                    body.studentId,
                    user["teacher_id"],
                    body.learningScore,
                    body.logicScore,
                    body.programmingScore,
                    body.timeScore,
                    total,
                    body.comment,
                ),
            )
            cur.execute(
                """
                SELECT score_id, total_score, status, submitted_at
                FROM interview_score
                WHERE session_id = %s AND intent_id = %s AND judge_teacher_id = %s
                """,
                (session_id, participant["intent_id"], user["teacher_id"]),
            )
            score = cur.fetchone()
            write_audit(
                cur,
                user["account_id"],
                "TEACHER",
                "INTERVIEW_SCORE",
                "INTERVIEW_SCORE",
                score["score_id"],
                None,
                {
                    "session_id": session_id,
                    "student_id": body.studentId,
                    "scores": dimensions,
                    "total_score": total,
                },
            )
            conn.commit()
            return {
                "scoreId": score["score_id"],
                "studentId": body.studentId,
                "dimensions": dimensions,
                "totalScore": float(score["total_score"]),
                "status": score["status"],
            }
    except HTTPException:
        conn.rollback()
        raise
    except Exception:
        conn.rollback()
        raise
    finally:
        conn.close()


@app.get("/api/interview/sessions/{session_id}/scores")
def list_interview_scores(session_id: int, user: dict = Depends(current_teacher)):
    conn = get_conn()
    try:
        with conn.cursor() as cur:
            cur.execute(
                """
                SELECT session_id, round_id, lab_id FROM interview_session
                WHERE session_id = %s
                """,
                (session_id,),
            )
            session = cur.fetchone()
            if not session:
                raise HTTPException(status_code=404, detail="面试场次不存在")
            roles = teacher_roles(cur, user["teacher_id"])
            is_manager = any(r["role_code"] == "COORDINATOR" for r in roles) or any(
                r["role_code"] == "LAB_OWNER" and r["scope_lab_id"] == session["lab_id"]
                for r in roles
            )
            if not is_manager:
                cur.execute(
                    "SELECT 1 FROM interview_judge WHERE session_id = %s AND teacher_id = %s",
                    (session_id, user["teacher_id"]),
                )
                allowed = cur.fetchone() is not None
                if not allowed:
                    cur.execute(
                        """
                        SELECT 1 FROM interview_participant p
                        JOIN intent i ON i.intent_id = p.intent_id
                        JOIN mentor m ON m.mentor_id = i.mentor_id
                        WHERE p.session_id = %s AND m.teacher_id = %s LIMIT 1
                        """,
                        (session_id, user["teacher_id"]),
                    )
                    allowed = cur.fetchone() is not None
                if not allowed:
                    raise HTTPException(status_code=403, detail="无权查看本场次评分")
            cur.execute(
                "SELECT round_type FROM selection_round WHERE round_id = %s",
                (session["round_id"],),
            )
            scheme = scheme_for_round(cur, cur.fetchone()["round_type"])
            cur.execute(
                """
                SELECT p.participant_id, p.student_id, st.student_no,
                       st.name AS student_name, p.intent_id, p.checkin_status,
                       (SELECT t.name FROM intent i
                        JOIN mentor m ON m.mentor_id = i.mentor_id
                        JOIN teacher t ON t.teacher_id = m.teacher_id
                        WHERE i.intent_id = p.intent_id) AS mentor_name,
                       (SELECT m2.teacher_id FROM intent i2
                        JOIN mentor m2 ON m2.mentor_id = i2.mentor_id
                        WHERE i2.intent_id = p.intent_id) AS mentor_teacher_id,
                       COUNT(s.score_id) AS judge_count,
                       ROUND(AVG(s.total_score), 2) AS avg_total
                FROM interview_participant p
                JOIN student st ON st.student_id = p.student_id
                LEFT JOIN interview_score s
                  ON s.session_id = p.session_id
                 AND s.intent_id = p.intent_id
                 AND s.status = 'SUBMITTED'
                WHERE p.session_id = %s
                GROUP BY p.participant_id, p.student_id, st.student_no,
                         st.name, p.intent_id, p.checkin_status
                ORDER BY avg_total DESC
                """,
                (session_id,),
            )
            items = cur.fetchall()
            if not is_manager:
                items = [i for i in items if i["mentor_teacher_id"] == user["teacher_id"]]
            cur.execute(
                """
                SELECT intent_id, score_id, learning_score, logic_score,
                       programming_score, time_score, total_score, comment, submitted_at
                FROM interview_score
                WHERE session_id = %s AND judge_teacher_id = %s AND status = 'SUBMITTED'
                """,
                (session_id, user["teacher_id"]),
            )
            mine = {row["intent_id"]: row for row in cur.fetchall()}
            for item in items:
                item["myScore"] = mine.get(item["intent_id"])
            return {
                "sessionId": session_id,
                "judgeCountMin": scheme["judge_count_min"],
                "items": items,
            }
    finally:
        conn.close()


@app.post("/api/results/propose")
def propose_admission(body: ProposeIn, user: dict = Depends(current_teacher)):
    conn = get_conn()
    try:
        with conn.cursor() as cur:
            cur.execute(
                """
                SELECT i.intent_id, i.student_id, i.mentor_id, i.lab_id, i.round_id,
                       i.status, l.name AS lab_name, l.quota,
                       r.round_type, r.round_name,
                       t.name AS mentor_name, st.name AS student_name
                FROM intent i
                JOIN lab l ON l.lab_id = i.lab_id
                JOIN selection_round r ON r.round_id = i.round_id
                JOIN mentor m ON m.mentor_id = i.mentor_id
                JOIN teacher t ON t.teacher_id = m.teacher_id
                JOIN student st ON st.student_id = i.student_id
                WHERE i.intent_id = %s
                """,
                (body.intentId,),
            )
            intent = cur.fetchone()
            if not intent:
                raise HTTPException(status_code=404, detail="意向不存在")
            if intent["status"] != "SUBMITTED":
                raise HTTPException(status_code=409, detail="该意向尚未提交")
            scope_mentor_id = mentor_scope(cur, user, intent["lab_id"])
            if scope_mentor_id is not None and intent["mentor_id"] != scope_mentor_id:
                raise HTTPException(status_code=403, detail="导师只能为选择自己的学生发起拟录取")
            if intent["round_type"] == "ADJUST":
                raise HTTPException(status_code=400, detail="统筹调剂轮次请走调剂流程")

            cur.execute(
                """
                SELECT p.session_id, p.checkin_status, s.start_time
                FROM interview_participant p
                JOIN interview_session s ON s.session_id = p.session_id
                WHERE p.intent_id = %s
                ORDER BY s.start_time DESC LIMIT 1
                """,
                (body.intentId,),
            )
            participant = cur.fetchone()
            if not participant:
                raise HTTPException(status_code=409, detail="该意向尚未安排面试")
            if participant["checkin_status"] != "CHECKED_IN":
                raise HTTPException(status_code=409, detail="学生未签到，不能拟录取")

            scheme = scheme_for_round(cur, intent["round_type"])
            summary = score_summary(cur, participant["session_id"], body.intentId)
            if summary["judge_count"] < 1:
                raise HTTPException(status_code=409, detail="该学生尚无评委评分，不能拟录取")
            if summary["avg_total"] is None:
                raise HTTPException(status_code=409, detail="该学生尚无有效评分")
            min_score = float(scheme["min_score"])
            for key in ("avg_learning", "avg_logic", "avg_programming", "avg_time"):
                if float(summary[key]) < min_score:
                    raise HTTPException(
                        status_code=409,
                        detail=f"评分未达方案最低分 {min_score:g}，不能拟录取",
                    )

            cur.execute(
                """
                SELECT admission_id FROM admission
                WHERE student_id = %s
                  AND status IN ('CONFIRMED','EFFECTIVE','ENROLLED')
                LIMIT 1
                """,
                (intent["student_id"],),
            )
            if cur.fetchone():
                raise HTTPException(status_code=409, detail="该学生已有确认录取，不能再拟录取")
            cur.execute(
                """
                SELECT result_id FROM selection_result
                WHERE round_id = %s AND lab_id = %s AND student_id = %s
                  AND list_type = 'PROPOSED'
                """,
                (intent["round_id"], intent["lab_id"], intent["student_id"]),
            )
            if cur.fetchone():
                raise HTTPException(status_code=409, detail="该学生已被本实训室拟录取")

            cur.execute("SELECT quota FROM lab WHERE lab_id = %s FOR UPDATE", (intent["lab_id"],))
            quota = cur.fetchone()["quota"]
            before = active_admission_count(cur, intent["lab_id"])
            if before >= quota:
                raise HTTPException(status_code=409, detail="该实训室名额已满")
            ensure_mentor_capacity(cur, intent["mentor_id"], intent["mentor_name"])

            cur.execute(
                """
                SELECT COALESCE(MAX(rank_no), 0) + 1 AS next_rank
                FROM selection_result
                WHERE round_id = %s AND lab_id = %s AND list_type = 'PROPOSED'
                """,
                (intent["round_id"], intent["lab_id"]),
            )
            rank_no = cur.fetchone()["next_rank"]
            avg_total = float(summary["avg_total"])
            cur.execute(
                """
                INSERT INTO selection_result
                  (round_id, lab_id, student_id, intent_id, list_type, rank_no,
                   score, generated_by, remark)
                VALUES (%s, %s, %s, %s, 'PROPOSED', %s, %s, %s, %s)
                """,
                (
                    intent["round_id"],
                    intent["lab_id"],
                    intent["student_id"],
                    body.intentId,
                    rank_no,
                    avg_total,
                    user["teacher_id"],
                    body.remark,
                ),
            )
            result_id = cur.lastrowid
            source_type = {
                "FIRST": "ROUND1",
                "SECOND": "ROUND2",
                "THIRD": "ROUND3",
            }[intent["round_type"]]
            cur.execute(
                """
                INSERT INTO admission
                  (student_id, lab_id, mentor_id, round_id, source_type,
                   confirm_type, status, confirm_deadline, reason)
                VALUES (%s, %s, %s, %s, %s, 'MANUAL', 'OCCUPIED',
                        DATE_ADD(NOW(3), INTERVAL 12 HOUR), %s)
                """,
                (
                    intent["student_id"],
                    intent["lab_id"],
                    intent["mentor_id"],
                    intent["round_id"],
                    source_type,
                    body.remark,
                ),
            )
            admission_id = cur.lastrowid
            after = active_admission_count(cur, intent["lab_id"])
            cur.execute(
                """
                INSERT INTO lab_quota_log
                  (lab_id, round_id, change_type, delta, active_count_before,
                   active_count_after, admission_id, operator_id, remark)
                VALUES (%s, %s, 'OCCUPY', 1, %s, %s, %s, %s, '拟录取锁定名额')
                """,
                (intent["lab_id"], intent["round_id"], before, after, admission_id, user["teacher_id"]),
            )
            notify_student(
                cur,
                intent["student_id"],
                "拟录取通知",
                (
                    f"你收到{intent['lab_name']}（导师：{intent['mentor_name']}）的拟录取意向，"
                    "请在 12 小时内登录系统确认，逾期视为放弃。"
                ),
                "ADMISSION",
                admission_id,
            )
            write_audit(
                cur,
                user["account_id"],
                "TEACHER",
                "ADMISSION_PROPOSE",
                "SELECTION_RESULT",
                result_id,
                None,
                {
                    "intent_id": body.intentId,
                    "student_id": intent["student_id"],
                    "lab_id": intent["lab_id"],
                    "mentor_id": intent["mentor_id"],
                    "avg_score": avg_total,
                    "admission_id": admission_id,
                },
            )
            conn.commit()
            cur.execute(
                "SELECT confirm_deadline FROM admission WHERE admission_id = %s",
                (admission_id,),
            )
            deadline = cur.fetchone()["confirm_deadline"]
            return {
                "resultId": result_id,
                "admissionId": admission_id,
                "studentId": intent["student_id"],
                "labName": intent["lab_name"],
                "mentorName": intent["mentor_name"],
                "score": avg_total,
                "rankNo": rank_no,
                "confirmDeadline": deadline,
            }
    except HTTPException:
        conn.rollback()
        raise
    except Exception:
        conn.rollback()
        raise
    finally:
        conn.close()


@app.get("/api/results/my-proposals")
def my_proposals(user: dict = Depends(current_student)):
    conn = get_conn()
    try:
        with conn.cursor() as cur:
            cur.execute(
                """
                SELECT r.result_id, r.round_id, r.lab_id, l.name AS lab_name,
                       r.intent_id, r.score, r.rank_no, r.remark, r.generated_at,
                       a.admission_id, a.status AS admission_status,
                       a.confirm_deadline, a.confirmed_at
                FROM selection_result r
                JOIN lab l ON l.lab_id = r.lab_id
                JOIN admission a ON a.admission_id = (
                    SELECT a2.admission_id FROM admission a2
                    WHERE a2.student_id = r.student_id
                      AND a2.lab_id = r.lab_id
                      AND a2.round_id = r.round_id
                    ORDER BY a2.admission_id DESC LIMIT 1
                )
                WHERE r.student_id = %s AND r.list_type = 'PROPOSED'
                ORDER BY r.generated_at DESC
                """,
                (user["student_id"],),
            )
            return cur.fetchall()
    finally:
        conn.close()


@app.post("/api/results/{result_id}/confirm")
def confirm_admission(result_id: int, user: dict = Depends(current_student)):
    conn = get_conn()
    try:
        with conn.cursor() as cur:
            cur.execute(
                """
                SELECT r.result_id, r.student_id, r.lab_id, r.round_id, r.intent_id,
                       r.score, a.admission_id, a.status AS admission_status,
                       a.confirm_deadline
                FROM selection_result r
                JOIN admission a ON a.admission_id = (
                    SELECT a2.admission_id FROM admission a2
                    WHERE a2.student_id = r.student_id
                      AND a2.lab_id = r.lab_id
                      AND a2.round_id = r.round_id
                    ORDER BY a2.admission_id DESC LIMIT 1
                )
                WHERE r.result_id = %s AND r.student_id = %s
                """,
                (result_id, user["student_id"]),
            )
            result = cur.fetchone()
            if not result:
                raise HTTPException(status_code=404, detail="拟录取记录不存在")
            if result["admission_status"] != "OCCUPIED":
                raise HTTPException(status_code=409, detail="该拟录取记录已处理")
            cur.execute(
                "SELECT * FROM admission WHERE admission_id = %s FOR UPDATE",
                (result["admission_id"],),
            )
            cur.execute(
                """
                SELECT admission_id FROM admission
                WHERE student_id = %s AND status = 'OCCUPIED' AND admission_id <> %s
                FOR UPDATE
                """,
                (user["student_id"], result["admission_id"]),
            )
            others = [r["admission_id"] for r in cur.fetchall()]
            for other_id in others:
                release_admission(cur, other_id, 0, "学生选择其他实训室，自动释放")
            cur.execute(
                """
                UPDATE admission
                SET status = 'CONFIRMED', confirmed_at = NOW(3), confirm_type = 'MANUAL'
                WHERE admission_id = %s
                """,
                (result["admission_id"],),
            )
            cur.execute(
                "UPDATE student SET selection_state = 'CONFIRMED' WHERE student_id = %s",
                (user["student_id"],),
            )
            write_audit(
                cur,
                user["account_id"],
                "STUDENT",
                "ADMISSION_CONFIRM",
                "ADMISSION",
                result["admission_id"],
                {"status": "OCCUPIED"},
                {"status": "CONFIRMED", "released_admission_ids": others},
            )
            conn.commit()
            return {
                "resultId": result_id,
                "admissionId": result["admission_id"],
                "status": "CONFIRMED",
                "releasedAdmissionIds": others,
            }
    except HTTPException:
        conn.rollback()
        raise
    except Exception:
        conn.rollback()
        raise
    finally:
        conn.close()


@app.post("/api/results/auto-confirm")
def auto_confirm_admissions(roundId: Optional[int] = None,
                            user: dict = Depends(current_teacher)):
    conn = get_conn()
    try:
        with conn.cursor() as cur:
            require_coordinator(cur, user)
            cur.execute(
                """
                SELECT a.student_id, MIN(a.admission_id) AS admission_id
                FROM admission a
                WHERE a.status = 'OCCUPIED'
                  AND (%s IS NULL OR a.round_id = %s)
                  AND a.confirm_deadline > NOW(3)
                GROUP BY a.student_id
                HAVING COUNT(*) = 1
                """,
                (roundId, roundId),
            )
            rows = cur.fetchall()
            confirmed = 0
            for row in rows:
                cur.execute(
                    """
                    UPDATE admission
                    SET status = 'CONFIRMED', confirmed_at = NOW(3), confirm_type = 'AUTO'
                    WHERE admission_id = %s AND status = 'OCCUPIED'
                    """,
                    (row["admission_id"],),
                )
                cur.execute(
                    "UPDATE student SET selection_state = 'CONFIRMED' WHERE student_id = %s",
                    (row["student_id"],),
                )
                write_audit(
                    cur,
                    user["account_id"],
                    "SYSTEM",
                    "ADMISSION_AUTO_CONFIRM",
                    "ADMISSION",
                    row["admission_id"],
                    {"status": "OCCUPIED"},
                    {"status": "CONFIRMED", "confirm_type": "AUTO"},
                )
                confirmed += 1
            conn.commit()
            return {"confirmed": confirmed}
    except HTTPException:
        conn.rollback()
        raise
    except Exception:
        conn.rollback()
        raise
    finally:
        conn.close()


@app.post("/api/results/{result_id}/abandon")
def abandon_proposal(result_id: int, body: Optional[AbandonIn] = None,
                     user: dict = Depends(current_student)):
    """学生主动放弃拟录取，释放名额并记录 ABANDONED。"""
    conn = get_conn()
    try:
        with conn.cursor() as cur:
            cur.execute(
                "SELECT * FROM selection_result WHERE result_id = %s AND student_id = %s",
                (result_id, user["student_id"]),
            )
            result = cur.fetchone()
            if not result:
                raise HTTPException(status_code=404, detail="拟录取记录不存在")
            cur.execute(
                """
                SELECT * FROM admission
                WHERE student_id = %s AND lab_id = %s AND round_id = %s
                  AND status IN ('OCCUPIED','CONFIRMED')
                ORDER BY admission_id DESC LIMIT 1
                """,
                (user["student_id"], result["lab_id"], result["round_id"]),
            )
            adm = cur.fetchone()
            if not adm:
                raise HTTPException(status_code=404, detail="录取记录不存在")
            if adm["status"] == "CONFIRMED":
                raise HTTPException(status_code=409, detail="已确认录取，如需放弃请联系管理员")
            reason = (body.reason if body and body.reason else "学生主动放弃拟录取")
            if not release_admission(cur, adm["admission_id"], 0, reason, status="ABANDONED"):
                raise HTTPException(status_code=409, detail="该录取记录已释放")
            cur.execute(
                "UPDATE student SET selection_state = 'NOT_FILLED' WHERE student_id = %s",
                (user["student_id"],),
            )
            write_audit(
                cur,
                user["account_id"],
                "STUDENT",
                "ADMISSION_ABANDON",
                "ADMISSION",
                adm["admission_id"],
                None,
                {"result_id": result_id, "reason": reason},
            )
            conn.commit()
            return {
                "resultId": result_id,
                "admissionId": adm["admission_id"],
                "status": "ABANDONED",
                "reason": reason,
            }
    except HTTPException:
        conn.rollback()
        raise
    except Exception:
        conn.rollback()
        raise
    finally:
        conn.close()


@app.post("/api/results/release-expired")
def release_expired_api(user: dict = Depends(current_teacher)):
    """统筹岗手动触发确认超时的拟录取释放。"""
    conn = get_conn()
    try:
        with conn.cursor() as cur:
            require_coordinator(cur, user)
    finally:
        conn.close()
    return release_expired_admissions()


@app.get("/api/labs")
def list_labs(user: dict = Depends(current_user)):
    conn = get_conn()
    try:
        with conn.cursor() as cur:
            cur.execute(
                """
                SELECT l.lab_id, l.lab_code, l.name, l.college, l.direction, l.quota,
                       l.training_plan, l.assessment_requirement, l.status,
                       (SELECT COUNT(*) FROM admission a
                        WHERE a.lab_id = l.lab_id
                          AND a.status IN ('OCCUPIED','CONFIRMED','EFFECTIVE','ENROLLED')
                       ) AS occupied,
                       (SELECT COUNT(*) FROM mentor m
                        WHERE m.lab_id = l.lab_id AND m.status = 'ACTIVE') AS mentor_count
                FROM lab l
                ORDER BY l.lab_id
                """
            )
            return {"items": cur.fetchall()}
    finally:
        conn.close()


@app.get("/api/labs/profiles")
def lab_profiles(user: dict = Depends(current_user)):
    """实训室信息（主要方向、简介、导师简介）：学生与教师均可只读查看。"""
    conn = get_conn()
    try:
        with conn.cursor() as cur:
            cur.execute(
                """
                SELECT l.lab_id, l.lab_code, l.name, l.college, l.direction, l.intro, l.quota
                FROM lab l
                ORDER BY l.lab_id
                """
            )
            labs = cur.fetchall()
            cur.execute(
                """
                SELECT m.mentor_id, m.lab_id, m.teacher_id, t.name, t.title, t.intro,
                       m.research_direction, m.capacity
                FROM mentor m
                JOIN teacher t ON t.teacher_id = m.teacher_id
                WHERE m.status = 'ACTIVE'
                ORDER BY m.lab_id, m.mentor_id
                """
            )
            mentors = cur.fetchall()
    finally:
        conn.close()
    grouped = {}
    for m in mentors:
        grouped.setdefault(m["lab_id"], []).append(m)
    for lab in labs:
        lab["mentors"] = grouped.get(lab["lab_id"], [])
    return {"items": labs}


@app.get("/api/teachers/me/duty")
def my_duty(user: dict = Depends(current_teacher)):
    """教师本人所属实训室及个人剩余录取名额（可带人数 - 已占名额）。"""
    conn = get_conn()
    try:
        with conn.cursor() as cur:
            cur.execute(
                """
                SELECT m.mentor_id, m.lab_id, l.name AS lab_name, m.capacity,
                       (SELECT COUNT(*) FROM admission a
                        WHERE a.mentor_id = m.mentor_id
                          AND a.status IN ('OCCUPIED','CONFIRMED','EFFECTIVE','ENROLLED')
                       ) AS occupied
                FROM mentor m
                JOIN lab l ON l.lab_id = m.lab_id
                WHERE m.teacher_id = %s AND m.status = 'ACTIVE'
                ORDER BY m.lab_id
                """,
                (user["teacher_id"],),
            )
            items = cur.fetchall()
    finally:
        conn.close()
    for item in items:
        capacity = item["capacity"]
        item["remaining"] = None if capacity is None else max(0, capacity - item["occupied"])
    return {"items": items}


@app.patch("/api/labs/{lab_id}/profile")
def update_lab_profile(lab_id: int, body: dict,
                       user: dict = Depends(current_teacher)):
    """实训室负责人（或统筹岗）维护本室主要方向与简介。"""
    if not isinstance(body, dict):
        raise HTTPException(status_code=400, detail="请求体格式错误")
    direction = str(body.get("direction") or "").strip()[:255]
    intro = str(body.get("intro") or "").strip()[:1000]
    conn = get_conn()
    try:
        with conn.cursor() as cur:
            require_lab_manager(cur, user, lab_id)
            cur.execute("SELECT direction, intro FROM lab WHERE lab_id = %s FOR UPDATE",
                        (lab_id,))
            before = cur.fetchone()
            if not before:
                raise HTTPException(status_code=404, detail="实训室不存在")
            cur.execute(
                "UPDATE lab SET direction = %s, intro = %s WHERE lab_id = %s",
                (direction or None, intro or None, lab_id),
            )
            write_audit(
                cur, user["account_id"], "TEACHER", "LAB_PROFILE_UPDATE", "LAB", lab_id,
                before, {"direction": direction, "intro": intro},
            )
        conn.commit()
        return {"labId": lab_id, "direction": direction, "intro": intro}
    except HTTPException:
        conn.rollback()
        raise
    finally:
        conn.close()


@app.patch("/api/teachers/me/intro")
def update_my_intro(body: dict, user: dict = Depends(current_teacher)):
    """教师维护自己的简介。"""
    if not isinstance(body, dict):
        raise HTTPException(status_code=400, detail="请求体格式错误")
    intro = str(body.get("intro") or "").strip()
    if len(intro) > 500:
        raise HTTPException(status_code=400, detail="简介不超过 500 字")
    conn = get_conn()
    try:
        with conn.cursor() as cur:
            cur.execute("SELECT intro FROM teacher WHERE teacher_id = %s FOR UPDATE",
                        (user["teacher_id"],))
            row = cur.fetchone()
            if not row:
                raise HTTPException(status_code=404, detail="教师不存在")
            cur.execute("UPDATE teacher SET intro = %s WHERE teacher_id = %s",
                        (intro or None, user["teacher_id"]))
            write_audit(
                cur, user["account_id"], "TEACHER", "TEACHER_INTRO_UPDATE", "TEACHER",
                user["teacher_id"], row, {"intro": intro},
            )
        conn.commit()
        return {"teacherId": user["teacher_id"], "intro": intro}
    except HTTPException:
        conn.rollback()
        raise
    finally:
        conn.close()


@app.get("/api/labs/{lab_id}/intents")
def lab_intents(lab_id: int, roundId: Optional[int] = None,
                user: dict = Depends(current_teacher)):
    """查看本室意向：负责人/统筹岗看全室，导师只看选择自己的学生。"""
    conn = get_conn()
    try:
        with conn.cursor() as cur:
            scope_mentor_id = mentor_scope(cur, user, lab_id)
            cur.execute("SELECT lab_id, lab_code, name, quota FROM lab WHERE lab_id = %s",
                        (lab_id,))
            lab_row = cur.fetchone()
            if not lab_row:
                raise HTTPException(status_code=404, detail="实训室不存在")
            cur.execute(
                """
                SELECT i.intent_id, i.student_id, s.student_no, s.name AS student_name,
                       s.major, s.programming_base, i.mentor_id, t.name AS mentor_name,
                       i.round_id, r.round_name, r.round_type, i.parallel_no, i.status,
                       i.submitted_at,
                       p.session_id, p.checkin_status,
                       ses.start_time AS interview_time, ses.location AS interview_location,
                       (SELECT ROUND(AVG(sc.total_score), 2) FROM interview_score sc
                        WHERE sc.intent_id = i.intent_id AND sc.status = 'SUBMITTED') AS avg_total,
                       (SELECT COUNT(*) FROM interview_score sc
                        WHERE sc.intent_id = i.intent_id AND sc.status = 'SUBMITTED') AS judge_count,
                       EXISTS(SELECT 1 FROM selection_result sr
                              WHERE sr.intent_id = i.intent_id AND sr.list_type = 'PROPOSED') AS proposed
                FROM intent i
                JOIN student s ON s.student_id = i.student_id
                JOIN selection_round r ON r.round_id = i.round_id
                JOIN mentor m ON m.mentor_id = i.mentor_id
                JOIN teacher t ON t.teacher_id = m.teacher_id
                LEFT JOIN interview_participant p ON p.intent_id = i.intent_id
                LEFT JOIN interview_session ses ON ses.session_id = p.session_id
                WHERE i.lab_id = %s AND (%s IS NULL OR i.round_id = %s)
                  AND (%s IS NULL OR i.mentor_id = %s)
                ORDER BY i.round_id, i.intent_id
                """,
                (lab_id, roundId, roundId, scope_mentor_id, scope_mentor_id),
            )
            items = cur.fetchall()
            for it in items:
                it["proposed"] = bool(it["proposed"])
            return {"lab": lab_row, "items": items}
    finally:
        conn.close()


@app.get("/api/labs/{lab_id}/mentors")
def lab_mentors(lab_id: int, user: dict = Depends(current_teacher)):
    """实训室导师列表，用于选择面试评委。"""
    conn = get_conn()
    try:
        with conn.cursor() as cur:
            require_lab_manager(cur, user, lab_id)
            cur.execute(
                """
                SELECT m.mentor_id, m.teacher_id, t.name AS teacher_name,
                       m.research_direction, m.capacity,
                       (SELECT COUNT(*) FROM admission a
                        WHERE a.mentor_id = m.mentor_id
                          AND a.status IN ('OCCUPIED','CONFIRMED','EFFECTIVE','ENROLLED')) AS load_cnt
                FROM mentor m
                JOIN teacher t ON t.teacher_id = m.teacher_id
                WHERE m.lab_id = %s AND m.status = 'ACTIVE'
                ORDER BY m.mentor_id
                """,
                (lab_id,),
            )
            return {"items": cur.fetchall()}
    finally:
        conn.close()


@app.get("/api/notifications")
def list_notifications(unreadOnly: bool = False, limit: int = 50,
                       user: dict = Depends(current_user)):
    conn = get_conn()
    try:
        with conn.cursor() as cur:
            cur.execute(
                """
                SELECT notice_id, notice_type, title, content, biz_type, biz_id,
                       read_flag, sent_at, read_at
                FROM notification
                WHERE receiver_account_id = %s
                  AND (%s = 0 OR read_flag = 0)
                ORDER BY sent_at DESC
                LIMIT %s
                """,
                (user["account_id"], 1 if unreadOnly else 0, limit),
            )
            items = cur.fetchall()
            cur.execute(
                "SELECT COUNT(*) AS c FROM notification WHERE receiver_account_id = %s AND read_flag = 0",
                (user["account_id"],),
            )
            unread = cur.fetchone()["c"]
            return {"items": items, "unreadCount": unread}
    finally:
        conn.close()


@app.post("/api/notifications/{notice_id}/read")
def read_notification(notice_id: int, user: dict = Depends(current_user)):
    conn = get_conn()
    try:
        with conn.cursor() as cur:
            cur.execute(
                """
                UPDATE notification SET read_flag = 1, read_at = NOW(3)
                WHERE notice_id = %s AND receiver_account_id = %s
                """,
                (notice_id, user["account_id"]),
            )
            if cur.rowcount == 0:
                raise HTTPException(status_code=404, detail="通知不存在")
            conn.commit()
            return {"noticeId": notice_id, "read": True}
    finally:
        conn.close()


@app.post("/api/notifications/read-all")
def read_all_notifications(user: dict = Depends(current_user)):
    conn = get_conn()
    try:
        with conn.cursor() as cur:
            cur.execute(
                "UPDATE notification SET read_flag = 1, read_at = NOW(3) "
                "WHERE receiver_account_id = %s AND read_flag = 0",
                (user["account_id"],),
            )
            conn.commit()
            return {"updated": cur.rowcount}
    finally:
        conn.close()


@app.post("/api/open-days/reservations")
def reserve_open_day(body: ReservationIn, user: dict = Depends(current_student)):
    conn = get_conn()
    try:
        with conn.cursor() as cur:
            cur.execute("SELECT lab_id, name, status FROM lab WHERE lab_id = %s", (body.labId,))
            lab_row = cur.fetchone()
            if not lab_row:
                raise HTTPException(status_code=404, detail="实训室不存在")
            if lab_row["status"] != "OPEN":
                raise HTTPException(status_code=409, detail="该实训室暂未开放参观")
            cur.execute(
                """
                INSERT INTO open_day_reservation
                  (student_id, lab_id, visit_date, time_slot, status, remark)
                VALUES (%s, %s, %s, %s, 'RESERVED', %s)
                ON DUPLICATE KEY UPDATE status = 'RESERVED', remark = VALUES(remark)
                """,
                (user["student_id"], body.labId, body.visitDate, body.timeSlot, body.remark),
            )
            cur.execute(
                """
                SELECT reservation_id, status FROM open_day_reservation
                WHERE student_id = %s AND lab_id = %s AND visit_date = %s
                  AND time_slot = %s
                """,
                (user["student_id"], body.labId, body.visitDate, body.timeSlot),
            )
            saved = cur.fetchone()
            conn.commit()
            return {
                "reservationId": saved["reservation_id"] if saved else None,
                "labId": body.labId,
                "labName": lab_row["name"],
                "visitDate": body.visitDate,
                "timeSlot": body.timeSlot,
                "status": saved["status"] if saved else "RESERVED",
            }
    except HTTPException:
        conn.rollback()
        raise
    finally:
        conn.close()


@app.get("/api/open-days/reservations/my")
def my_open_day_reservations(user: dict = Depends(current_student)):
    conn = get_conn()
    try:
        with conn.cursor() as cur:
            cur.execute(
                """
                SELECT r.reservation_id, r.lab_id, l.name AS lab_name, r.visit_date,
                       r.time_slot, r.status, r.remark, r.created_at
                FROM open_day_reservation r
                JOIN lab l ON l.lab_id = r.lab_id
                WHERE r.student_id = %s
                ORDER BY r.visit_date DESC, r.reservation_id DESC
                """,
                (user["student_id"],),
            )
            return {"items": cur.fetchall()}
    finally:
        conn.close()


@app.post("/api/open-days/reservations/{reservation_id}/cancel")
def cancel_open_day(reservation_id: int, user: dict = Depends(current_student)):
    conn = get_conn()
    try:
        with conn.cursor() as cur:
            cur.execute(
                """
                UPDATE open_day_reservation SET status = 'CANCELED'
                WHERE reservation_id = %s AND student_id = %s AND status = 'RESERVED'
                """,
                (reservation_id, user["student_id"]),
            )
            if cur.rowcount == 0:
                raise HTTPException(status_code=409, detail="预约不存在或不可取消")
            conn.commit()
            return {"reservationId": reservation_id, "status": "CANCELED"}
    finally:
        conn.close()


@app.get("/api/open-days/reservations")
def list_open_day_reservations(labId: Optional[int] = None,
                               visitDate: Optional[str] = None,
                               user: dict = Depends(current_teacher)):
    conn = get_conn()
    try:
        with conn.cursor() as cur:
            cur.execute(
                """
                SELECT r.reservation_id, r.student_id, s.student_no, s.name AS student_name,
                       r.lab_id, l.name AS lab_name, r.visit_date, r.time_slot,
                       r.status, r.remark
                FROM open_day_reservation r
                JOIN student s ON s.student_id = r.student_id
                JOIN lab l ON l.lab_id = r.lab_id
                WHERE (%s IS NULL OR r.lab_id = %s)
                  AND (%s IS NULL OR r.visit_date = %s)
                ORDER BY r.visit_date, r.time_slot, r.reservation_id
                """,
                (labId, labId, visitDate, visitDate),
            )
            return {"items": cur.fetchall()}
    finally:
        conn.close()


@app.post("/api/open-days/reservations/{reservation_id}/checkin")
def checkin_open_day(reservation_id: int, user: dict = Depends(current_teacher)):
    conn = get_conn()
    try:
        with conn.cursor() as cur:
            cur.execute(
                "UPDATE open_day_reservation SET status = 'CHECKED_IN' "
                "WHERE reservation_id = %s AND status = 'RESERVED'",
                (reservation_id,),
            )
            if cur.rowcount == 0:
                raise HTTPException(status_code=409, detail="预约不存在或已处理")
            conn.commit()
            return {"reservationId": reservation_id, "status": "CHECKED_IN"}
    finally:
        conn.close()


@app.get("/api/stages")
def list_stages(user: dict = Depends(current_user)):
    conn = get_conn()
    try:
        with conn.cursor() as cur:
            cur.execute(
                "SELECT stage_id, stage_no, stage_code, stage_name, start_time, end_time, "
                "status, opened_at, closed_at, remark FROM selection_stage ORDER BY stage_no"
            )
            return {"items": cur.fetchall()}
    finally:
        conn.close()


@app.post("/api/stages/{stage_id}/open")
def open_stage(stage_id: int, user: dict = Depends(current_teacher)):
    conn = get_conn()
    try:
        with conn.cursor() as cur:
            require_coordinator(cur, user)
            cur.execute(
                "UPDATE selection_stage SET status = 'OPEN', opened_by = %s, opened_at = NOW(3) "
                "WHERE stage_id = %s",
                (user["teacher_id"], stage_id),
            )
            if cur.rowcount == 0:
                raise HTTPException(status_code=404, detail="阶段不存在")
            conn.commit()
            return {"stageId": stage_id, "status": "OPEN"}
    finally:
        conn.close()


@app.post("/api/stages/{stage_id}/close")
def close_stage(stage_id: int, user: dict = Depends(current_teacher)):
    conn = get_conn()
    try:
        with conn.cursor() as cur:
            require_coordinator(cur, user)
            cur.execute(
                "UPDATE selection_stage SET status = 'CLOSED', closed_by = %s, closed_at = NOW(3) "
                "WHERE stage_id = %s",
                (user["teacher_id"], stage_id),
            )
            if cur.rowcount == 0:
                raise HTTPException(status_code=404, detail="阶段不存在")
            conn.commit()
            return {"stageId": stage_id, "status": "CLOSED"}
    finally:
        conn.close()


@app.get("/api/rounds")
def list_rounds(user: dict = Depends(current_user)):
    conn = get_conn()
    try:
        with conn.cursor() as cur:
            cur.execute(
                """
                SELECT round_id, round_no, round_type, round_name, intent_limit,
                       fill_start, fill_end, interview_start, interview_end,
                       confirm_start, confirm_end, status
                FROM selection_round ORDER BY round_no
                """
            )
            return {"items": cur.fetchall()}
    finally:
        conn.close()


@app.post("/api/rounds/{round_id}/open")
def open_round_api(round_id: int, user: dict = Depends(current_teacher)):
    conn = get_conn()
    try:
        with conn.cursor() as cur:
            require_coordinator(cur, user)
            cur.execute(
                "SELECT round_no FROM selection_round WHERE round_id = %s",
                (round_id,),
            )
            round_row = cur.fetchone()
            if not round_row:
                raise HTTPException(status_code=404, detail="轮次不存在")
            # 自由推进：开启新轮次时自动收起其它填报中的轮次
            cur.execute(
                "UPDATE selection_round SET status = 'CLOSED' "
                "WHERE status = 'FILLING' AND round_id <> %s",
                (round_id,),
            )
            cur.execute(
                "UPDATE selection_round SET status = 'FILLING', "
                "fill_start = LEAST(fill_start, NOW(3)), fill_end = GREATEST(fill_end, NOW(3)) "
                "WHERE round_id = %s",
                (round_id,),
            )

            # 新一轮开放时：仅一份拟录取的学生自动确认；多份未确认的释放名额
            cur.execute(
                """
                SELECT a.admission_id, a.student_id, a.lab_id, l.name AS lab_name
                FROM admission a
                JOIN selection_round r ON r.round_id = a.round_id
                JOIN lab l ON l.lab_id = a.lab_id
                WHERE a.status = 'OCCUPIED' AND r.round_no < %s
                ORDER BY a.student_id, a.admission_id
                """,
                (round_row["round_no"],),
            )
            unconfirmed = cur.fetchall()
            grouped_unconfirmed = {}
            for row in unconfirmed:
                grouped_unconfirmed.setdefault(row["student_id"], []).append(row)

            auto_confirmed_ids = []
            released_ids = []
            released_students = set()
            for student_id, items in grouped_unconfirmed.items():
                # 学生只有一份拟录取结果：进入下一轮时未确认则自动确认
                if len(items) == 1:
                    admission_id = items[0]["admission_id"]
                    cur.execute(
                        """
                        UPDATE admission
                        SET status = 'CONFIRMED', confirmed_at = NOW(3), confirm_type = 'AUTO'
                        WHERE admission_id = %s AND status = 'OCCUPIED'
                        """,
                        (admission_id,),
                    )
                    if cur.rowcount == 0:
                        continue
                    cur.execute(
                        "UPDATE student SET selection_state = 'CONFIRMED' WHERE student_id = %s",
                        (student_id,),
                    )
                    notify_student(
                        cur,
                        student_id,
                        "拟录取已自动确认",
                        (
                            f"你在{items[0]['lab_name']}的拟录取为唯一一份拟录取结果，"
                            "进入新一轮次时系统已自动确认，请关注后续公示与入组安排。"
                        ),
                        "ADMISSION",
                        admission_id,
                    )
                    write_audit(
                        cur,
                        user["account_id"],
                        "SYSTEM",
                        "ADMISSION_AUTO_CONFIRM",
                        "ADMISSION",
                        admission_id,
                        {"status": "OCCUPIED"},
                        {
                            "status": "CONFIRMED",
                            "confirm_type": "AUTO",
                            "trigger": "ROUND_OPEN",
                            "opened_round_id": round_id,
                        },
                    )
                    auto_confirmed_ids.append(admission_id)
                    continue
                for row in items:
                    if not release_admission(
                        cur, row["admission_id"], user["account_id"],
                        "新一轮次开放，上一轮未确认自动释放",
                    ):
                        continue
                    notify_student(
                        cur,
                        row["student_id"],
                        "上一轮拟录取已释放",
                        "新一轮次已开放，你在上一轮的拟录取未确认，名额已自动释放，可参加本轮补录。",
                        "ADMISSION",
                        row["admission_id"],
                    )
                    write_audit(
                        cur,
                        user["account_id"],
                        "SYSTEM",
                        "ADMISSION_RELEASE_ROUND_OPEN",
                        "ADMISSION",
                        row["admission_id"],
                        None,
                        {
                            "student_id": row["student_id"],
                            "lab_id": row["lab_id"],
                            "opened_round_id": round_id,
                        },
                    )
                    released_ids.append(row["admission_id"])
                    released_students.add(row["student_id"])

            supplement_students = []
            for student_id in released_students:
                cur.execute(
                    """
                    SELECT 1 FROM admission
                    WHERE student_id = %s
                      AND status IN ('OCCUPIED','CONFIRMED','EFFECTIVE','ENROLLED')
                    LIMIT 1
                    """,
                    (student_id,),
                )
                if cur.fetchone():
                    continue
                cur.execute(
                    "UPDATE student SET selection_state = 'SUPPLEMENT' WHERE student_id = %s",
                    (student_id,),
                )
                notify_student(
                    cur,
                    student_id,
                    "已进入补录",
                    "你上一轮的拟录取未确认已释放，已进入补录，请在本轮重新填报。",
                    "STUDENT",
                    student_id,
                )
                write_audit(
                    cur,
                    user["account_id"],
                    "SYSTEM",
                    "STUDENT_ENTER_SUPPLEMENT",
                    "STUDENT",
                    student_id,
                    None,
                    {"reason": "ROUND_OPEN_RELEASE", "opened_round_id": round_id},
                )
                supplement_students.append(student_id)

            # 进入新一轮次后，撤销此前轮次的面试场次（已有评分的场次保留）
            cur.execute(
                """
                SELECT s.session_id, s.session_name, s.start_time, s.status
                FROM interview_session s
                JOIN selection_round r ON r.round_id = s.round_id
                WHERE r.round_no < %s AND s.status <> 'CANCELED'
                ORDER BY s.session_id
                """,
                (round_row["round_no"],),
            )
            old_sessions = cur.fetchall()
            canceled_session_ids = []
            kept_scored_session_ids = []
            for session in old_sessions:
                cur.execute(
                    "SELECT COUNT(*) AS c FROM interview_score WHERE session_id = %s",
                    (session["session_id"],),
                )
                if cur.fetchone()["c"] > 0:
                    kept_scored_session_ids.append(session["session_id"])
                    continue
                cur.execute(
                    """
                    SELECT DISTINCT p.student_id, a.account_id
                    FROM interview_participant p
                    JOIN student s ON s.student_id = p.student_id
                    JOIN account a ON a.account_id = s.account_id
                    WHERE p.session_id = %s
                    """,
                    (session["session_id"],),
                )
                students = cur.fetchall()
                cur.execute(
                    "DELETE FROM interview_reminder WHERE session_id = %s",
                    (session["session_id"],),
                )
                cur.execute(
                    "DELETE FROM interview_participant WHERE session_id = %s",
                    (session["session_id"],),
                )
                cur.execute(
                    "DELETE FROM interview_judge WHERE session_id = %s",
                    (session["session_id"],),
                )
                cur.execute(
                    "UPDATE interview_session SET status = 'CANCELED' WHERE session_id = %s",
                    (session["session_id"],),
                )
                for row in students:
                    cur.execute(
                        """
                        INSERT INTO notification
                          (receiver_account_id, notice_type, title, content,
                           biz_type, biz_id, sent_at)
                        VALUES (%s, 'INTERVIEW', '面试场次已撤销', %s,
                                'INTERVIEW_SESSION', %s, NOW(3))
                        """,
                        (
                            row["account_id"],
                            f"新一轮次已开放，原安排的面试场次“{session['session_name'] or '面试场次'}”"
                            f"（{session['start_time'].strftime('%Y-%m-%d %H:%M')}）已撤销，"
                            "请关注新的面试安排通知。",
                            session["session_id"],
                        ),
                    )
                write_audit(
                    cur,
                    user["account_id"],
                    "SYSTEM",
                    "INTERVIEW_SESSION_CANCEL",
                    "INTERVIEW_SESSION",
                    session["session_id"],
                    {"status": session["status"], "participants": len(students)},
                    {
                        "status": "CANCELED",
                        "trigger": "ROUND_OPEN",
                        "opened_round_id": round_id,
                        "removed_participants": len(students),
                    },
                )
                canceled_session_ids.append(session["session_id"])

            conn.commit()
            return {
                "roundId": round_id,
                "status": "FILLING",
                "released": len(released_ids),
                "autoConfirmed": len(auto_confirmed_ids),
                "autoConfirmedIds": auto_confirmed_ids,
                "supplementStudents": supplement_students,
                "canceledSessions": len(canceled_session_ids),
                "canceledSessionIds": canceled_session_ids,
                "keptScoredSessionIds": kept_scored_session_ids,
            }
    finally:
        conn.close()


@app.post("/api/rounds/{round_id}/close")
def close_round_api(round_id: int, user: dict = Depends(current_teacher)):
    conn = get_conn()
    try:
        with conn.cursor() as cur:
            require_coordinator(cur, user)
            cur.execute(
                "SELECT round_type, round_name FROM selection_round WHERE round_id = %s",
                (round_id,),
            )
            round_row = cur.fetchone()
            if not round_row:
                raise HTTPException(status_code=404, detail="轮次不存在")
            # 统筹调剂是最后环节：关闭前必须确保每名学生都已有实训室
            if round_row["round_type"] == "ADJUST":
                cur.execute(
                    """
                    SELECT COUNT(*) AS unplaced FROM student s
                    WHERE NOT EXISTS (
                            SELECT 1 FROM admission a
                            WHERE a.student_id = s.student_id
                              AND a.status IN ('OCCUPIED','CONFIRMED','EFFECTIVE','ENROLLED'))
                    """
                )
                unplaced = cur.fetchone()["unplaced"]
                if unplaced:
                    raise HTTPException(
                        status_code=409,
                        detail=f"仍有 {unplaced} 名学生未落实实训室，请通过约谈完成安置后再关闭调剂轮次",
                    )
            cur.execute(
                "UPDATE selection_round SET status = 'CLOSED', fill_end = NOW(3) WHERE round_id = %s",
                (round_id,),
            )
            if cur.rowcount == 0:
                raise HTTPException(status_code=404, detail="轮次不存在")

            # 轮次关闭时处理该轮学生的拟录取确认：
            # 仅 1 个拟录取 -> 自动确认；2~3 个 -> 提醒确认并设置 30 分钟确认截止
            cur.execute(
                """
                SELECT a.student_id, a.admission_id, l.name AS lab_name
                FROM admission a
                JOIN lab l ON l.lab_id = a.lab_id
                WHERE a.status = 'OCCUPIED'
                  AND a.student_id IN (
                    SELECT DISTINCT student_id FROM admission
                    WHERE round_id = %s AND status = 'OCCUPIED'
                  )
                ORDER BY a.student_id, a.admission_id
                """,
                (round_id,),
            )
            grouped = {}
            for row in cur.fetchall():
                grouped.setdefault(row["student_id"], []).append(row)

            auto_confirmed = 0
            reminded = 0
            for student_id, items in grouped.items():
                if len(items) == 1:
                    admission_id = items[0]["admission_id"]
                    cur.execute(
                        """
                        UPDATE admission
                        SET status = 'CONFIRMED', confirmed_at = NOW(3), confirm_type = 'AUTO'
                        WHERE admission_id = %s AND status = 'OCCUPIED'
                        """,
                        (admission_id,),
                    )
                    if cur.rowcount == 0:
                        continue
                    cur.execute(
                        "UPDATE student SET selection_state = 'CONFIRMED' WHERE student_id = %s",
                        (student_id,),
                    )
                    notify_student(
                        cur,
                        student_id,
                        "拟录取已自动确认",
                        (
                            f"你在{items[0]['lab_name']}的拟录取为唯一志愿，轮次关闭时系统已自动确认，"
                            "请关注后续公示与入组安排。"
                        ),
                        "ADMISSION",
                        admission_id,
                    )
                    write_audit(
                        cur,
                        user["account_id"],
                        "SYSTEM",
                        "ADMISSION_AUTO_CONFIRM",
                        "ADMISSION",
                        admission_id,
                        {"status": "OCCUPIED"},
                        {"status": "CONFIRMED", "confirm_type": "AUTO", "trigger": "ROUND_CLOSE"},
                    )
                    auto_confirmed += 1
                else:
                    admission_ids = [it["admission_id"] for it in items]
                    placeholders = ",".join(["%s"] * len(admission_ids))
                    cur.execute(
                        f"""
                        UPDATE admission
                        SET confirm_deadline = DATE_ADD(NOW(3), INTERVAL 30 MINUTE)
                        WHERE admission_id IN ({placeholders}) AND status = 'OCCUPIED'
                        """,
                        tuple(admission_ids),
                    )
                    cur.execute(
                        "UPDATE student SET selection_state = 'PENDING_CONFIRM' WHERE student_id = %s",
                        (student_id,),
                    )
                    lab_text = "、".join(it["lab_name"] for it in items)
                    notify_student(
                        cur,
                        student_id,
                        "拟录取待确认提醒",
                        (
                            f"你在本轮共有 {len(items)} 个拟录取待确认（{lab_text}），"
                            "请于 30 分钟内登录系统确认其中一个；"
                            "逾期未确认将自动释放全部名额，并进入补录。"
                        ),
                        "ADMISSION",
                        admission_ids[0],
                    )
                    write_audit(
                        cur,
                        user["account_id"],
                        "SYSTEM",
                        "ADMISSION_CONFIRM_REMIND",
                        "STUDENT",
                        student_id,
                        None,
                        {"occupied_admission_ids": admission_ids, "deadline_minutes": 30},
                    )
                    reminded += 1

            conn.commit()
            return {
                "roundId": round_id,
                "status": "CLOSED",
                "autoConfirmed": auto_confirmed,
                "reminded": reminded,
                "confirmDeadlineMinutes": 30,
            }
    except HTTPException:
        conn.rollback()
        raise
    except Exception:
        conn.rollback()
        raise
    finally:
        conn.close()


@app.post("/api/publicity/batches")
def publish_batch(body: PublicityBatchIn, user: dict = Depends(current_teacher)):
    """发布公示批次，自动收录该轮有效录取结果。"""
    conn = get_conn()
    try:
        with conn.cursor() as cur:
            require_coordinator(cur, user)
            cur.execute(
                "SELECT batch_id FROM publicity_batch "
                "WHERE round_id <=> %s AND status = 'PUBLISHING' LIMIT 1",
                (body.roundId,),
            )
            if cur.fetchone():
                raise HTTPException(status_code=409, detail="该轮次已有进行中的公示批次")
            batch_no = f"PUB-{body.roundId or 0}-{datetime.now().strftime('%Y%m%d%H%M%S')}"
            cur.execute(
                """
                INSERT INTO publicity_batch
                  (round_id, batch_no, start_time, end_time, status, published_by, remark)
                VALUES (%s, %s, %s, %s, 'PUBLISHING', %s, %s)
                """,
                (body.roundId, batch_no, body.startTime, body.endTime,
                 user["teacher_id"], body.remark),
            )
            batch_id = cur.lastrowid
            cur.execute(
                """
                SELECT admission_id, student_id, lab_id, mentor_id
                FROM admission
                WHERE (%s IS NULL OR round_id = %s)
                  AND status IN ('OCCUPIED','CONFIRMED','EFFECTIVE','ENROLLED')
                """,
                (body.roundId, body.roundId),
            )
            admissions = cur.fetchall()
            for row in admissions:
                cur.execute(
                    """
                    INSERT INTO publicity_item
                      (batch_id, student_id, lab_id, mentor_id, admission_id, item_status)
                    VALUES (%s, %s, %s, %s, %s, 'NORMAL')
                    """,
                    (batch_id, row["student_id"], row["lab_id"], row["mentor_id"],
                     row["admission_id"]),
                )
            for row in admissions:
                notify_student(
                    cur, row["student_id"], "选拔结果公示",
                    f"第 {batch_id} 批选拔结果已公示，如有异议请在公示期内提交。",
                    "PUBLICITY", batch_id,
                )
            write_audit(
                cur, user["account_id"], "TEACHER", "PUBLICITY_PUBLISH",
                "PUBLICITY_BATCH", batch_id, None,
                {"round_id": body.roundId, "items": len(admissions)},
            )
            conn.commit()
            return {"batchId": batch_id, "batchNo": batch_no, "items": len(admissions)}
    except HTTPException:
        conn.rollback()
        raise
    finally:
        conn.close()


@app.get("/api/publicity/batches")
def list_publicity_batches(user: dict = Depends(current_user)):
    conn = get_conn()
    try:
        with conn.cursor() as cur:
            cur.execute(
                """
                SELECT b.batch_id, b.round_id, r.round_name, b.batch_no, b.start_time,
                       b.end_time, b.status, b.published_at, b.remark,
                       (SELECT COUNT(*) FROM publicity_item i WHERE i.batch_id = b.batch_id) AS item_count
                FROM publicity_batch b
                LEFT JOIN selection_round r ON r.round_id = b.round_id
                ORDER BY b.batch_id DESC
                """
            )
            return {"items": cur.fetchall()}
    finally:
        conn.close()


@app.get("/api/publicity/batches/{batch_id}")
def get_publicity_batch(batch_id: int, user: dict = Depends(current_user)):
    conn = get_conn()
    try:
        with conn.cursor() as cur:
            cur.execute("SELECT * FROM publicity_batch WHERE batch_id = %s", (batch_id,))
            batch = cur.fetchone()
            if not batch:
                raise HTTPException(status_code=404, detail="公示批次不存在")
            sql = """
                SELECT i.item_id, i.student_id, s.student_no, s.name AS student_name,
                       i.lab_id, l.name AS lab_name, i.mentor_id, t.name AS mentor_name,
                       i.admission_id, i.item_status
                FROM publicity_item i
                JOIN student s ON s.student_id = i.student_id
                JOIN lab l ON l.lab_id = i.lab_id
                JOIN mentor m ON m.mentor_id = i.mentor_id
                JOIN teacher t ON t.teacher_id = m.teacher_id
                WHERE i.batch_id = %s
            """
            params = [batch_id]
            sql += " ORDER BY i.lab_id, i.item_id"
            cur.execute(sql, tuple(params))
            batch["items"] = cur.fetchall()
            return batch
    finally:
        conn.close()


@app.post("/api/publicity/batches/{batch_id}/finish")
def finish_publicity(batch_id: int, user: dict = Depends(current_teacher)):
    conn = get_conn()
    try:
        with conn.cursor() as cur:
            require_coordinator(cur, user)
            cur.execute(
                "UPDATE publicity_batch SET status = 'FINISHED' "
                "WHERE batch_id = %s AND status = 'PUBLISHING'",
                (batch_id,),
            )
            if cur.rowcount == 0:
                raise HTTPException(status_code=409, detail="批次不存在或已结束")
            conn.commit()
            return {"batchId": batch_id, "status": "FINISHED"}
    finally:
        conn.close()


@app.get("/api/publicity/my")
def my_publicity(user: dict = Depends(current_student)):
    conn = get_conn()
    try:
        with conn.cursor() as cur:
            cur.execute(
                """
                SELECT i.item_id, i.batch_id, b.batch_no, b.status AS batch_status,
                       b.start_time, b.end_time, i.lab_id, l.name AS lab_name,
                       i.mentor_id, t.name AS mentor_name, i.item_status
                FROM publicity_item i
                JOIN publicity_batch b ON b.batch_id = i.batch_id
                JOIN lab l ON l.lab_id = i.lab_id
                JOIN mentor m ON m.mentor_id = i.mentor_id
                JOIN teacher t ON t.teacher_id = m.teacher_id
                WHERE i.student_id = %s
                ORDER BY i.batch_id DESC
                """,
                (user["student_id"],),
            )
            return {"items": cur.fetchall()}
    finally:
        conn.close()


@app.post("/api/objections")
def create_objection(body: ObjectionIn, user: dict = Depends(current_student)):
    conn = get_conn()
    try:
        with conn.cursor() as cur:
            cur.execute(
                "SELECT batch_id, status FROM publicity_batch WHERE batch_id = %s",
                (body.batchId,),
            )
            batch = cur.fetchone()
            if not batch:
                raise HTTPException(status_code=404, detail="公示批次不存在")
            content = (body.content or "").strip()
            if len(content) < 5:
                raise HTTPException(status_code=400, detail="异议内容至少 5 个字")
            cur.execute(
                """
                INSERT INTO objection
                  (batch_id, publicity_item_id, student_id, content, status)
                VALUES (%s, %s, %s, %s, 'PENDING')
                """,
                (body.batchId, body.publicityItemId, user["student_id"], content),
            )
            objection_id = cur.lastrowid
            write_audit(
                cur, user["account_id"], "STUDENT", "OBJECTION_CREATE",
                "OBJECTION", objection_id, None, {"batch_id": body.batchId},
            )
            conn.commit()
            return {"objectionId": objection_id, "status": "PENDING"}
    except HTTPException:
        conn.rollback()
        raise
    finally:
        conn.close()


@app.get("/api/objections/my")
def my_objections(user: dict = Depends(current_student)):
    conn = get_conn()
    try:
        with conn.cursor() as cur:
            cur.execute(
                """
                SELECT o.objection_id, o.batch_id, b.batch_no, o.content, o.status,
                       o.resolution, o.created_at, o.handled_at
                FROM objection o
                JOIN publicity_batch b ON b.batch_id = o.batch_id
                WHERE o.student_id = %s
                ORDER BY o.objection_id DESC
                """,
                (user["student_id"],),
            )
            return {"items": cur.fetchall()}
    finally:
        conn.close()


@app.get("/api/objections")
def list_objections(status: Optional[str] = None, user: dict = Depends(current_teacher)):
    conn = get_conn()
    try:
        with conn.cursor() as cur:
            require_coordinator(cur, user)
            cur.execute(
                """
                SELECT o.objection_id, o.batch_id, b.batch_no, o.student_id,
                       s.student_no, s.name AS student_name, o.content, o.status,
                       o.resolution, o.created_at, o.handled_at, o.handled_by
                FROM objection o
                JOIN publicity_batch b ON b.batch_id = o.batch_id
                JOIN student s ON s.student_id = o.student_id
                WHERE (%s IS NULL OR o.status = %s)
                ORDER BY o.objection_id DESC
                """,
                (status, status),
            )
            return {"items": cur.fetchall()}
    finally:
        conn.close()


@app.post("/api/objections/{objection_id}/handle")
def handle_objection(objection_id: int, body: ObjectionHandleIn,
                     user: dict = Depends(current_teacher)):
    conn = get_conn()
    try:
        with conn.cursor() as cur:
            require_coordinator(cur, user)
            if body.status not in ("PROCESSING", "RESOLVED", "REJECTED"):
                raise HTTPException(status_code=400, detail="status 仅支持 PROCESSING/RESOLVED/REJECTED")
            cur.execute(
                "UPDATE objection SET status = %s, handled_by = %s, handled_at = NOW(3), "
                "resolution = %s WHERE objection_id = %s",
                (body.status, user["teacher_id"], body.resolution, objection_id),
            )
            if cur.rowcount == 0:
                raise HTTPException(status_code=404, detail="异议不存在")
            cur.execute("SELECT student_id, batch_id FROM objection WHERE objection_id = %s",
                        (objection_id,))
            row = cur.fetchone()
            notify_student(
                cur, row["student_id"], "异议处理结果",
                f"你对第 {row['batch_id']} 批公示的异议已处理：{body.status}。"
                f"{body.resolution or ''}",
                "OBJECTION", objection_id,
            )
            write_audit(
                cur, user["account_id"], "TEACHER", "OBJECTION_HANDLE",
                "OBJECTION", objection_id, None,
                {"status": body.status, "resolution": body.resolution},
            )
            conn.commit()
            return {"objectionId": objection_id, "status": body.status}
    except HTTPException:
        conn.rollback()
        raise
    finally:
        conn.close()


@app.get("/api/stats/overview")
def stats_overview(user: dict = Depends(current_teacher)):
    conn = get_conn()
    try:
        with conn.cursor() as cur:
            cur.execute(
                """
                SELECT l.lab_id, l.lab_code, l.name AS lab_name, l.quota,
                       (SELECT COUNT(*) FROM admission a
                        WHERE a.lab_id = l.lab_id
                          AND a.status IN ('OCCUPIED','CONFIRMED','EFFECTIVE','ENROLLED')) AS occupied,
                       (SELECT COUNT(DISTINCT i.student_id) FROM intent i
                        WHERE i.lab_id = l.lab_id) AS applicants,
                       (SELECT COUNT(*) FROM interview_session s
                        WHERE s.lab_id = l.lab_id AND s.status <> 'CANCELED') AS session_count,
                       (SELECT COUNT(*) FROM open_day_reservation r
                        WHERE r.lab_id = l.lab_id AND r.status = 'RESERVED') AS reservations
                FROM lab l ORDER BY l.lab_id
                """
            )
            labs = cur.fetchall()
            cur.execute(
                """
                SELECT (SELECT COUNT(*) FROM intent_application WHERE status='SUBMITTED') AS submitted,
                       (SELECT COUNT(*) FROM admission WHERE status='OCCUPIED') AS occupied,
                       (SELECT COUNT(*) FROM admission WHERE status='CONFIRMED') AS confirmed,
                       (SELECT COUNT(*) FROM adjustment WHERE status='PENDING') AS pending_negotiation,
                       (SELECT COUNT(*) FROM objection WHERE status='PENDING') AS pending_objection
                """
            )
            summary = cur.fetchone()
            return {"labs": labs, "summary": summary}
    finally:
        conn.close()


@app.get("/api/stats/submitted-applications")
def submitted_applications(user: dict = Depends(current_teacher)):
    """概览“已提交申请”点击查看：按权限返回已提交的意向申请清单。"""
    conn = get_conn()
    try:
        with conn.cursor() as cur:
            is_coordinator, scope_labs = teacher_lab_scope(cur, user["teacher_id"])

            scope_sql = ""
            params = []
            if not is_coordinator:
                if not scope_labs:
                    return {"items": [], "total": 0}
                placeholders = ",".join(["%s"] * len(scope_labs))
                scope_sql = f" AND i.lab_id IN ({placeholders})"
                params = list(scope_labs)

            cur.execute(
                f"""
                SELECT ia.application_id, ia.submitted_at, ia.accept_internal_adjust,
                       ia.accept_cross_adjust, s.student_no, s.name AS student_name,
                       s.grade, s.major, r.round_name, r.round_no,
                       COUNT(i.intent_id) AS intent_count,
                       GROUP_CONCAT(
                         CONCAT(l.name, '·', t.name)
                         ORDER BY i.parallel_no SEPARATOR '、'
                       ) AS intents
                FROM intent_application ia
                JOIN student s ON s.student_id = ia.student_id
                JOIN selection_round r ON r.round_id = ia.round_id
                JOIN intent i ON i.application_id = ia.application_id
                JOIN mentor m ON m.mentor_id = i.mentor_id
                JOIN teacher t ON t.teacher_id = m.teacher_id
                JOIN lab l ON l.lab_id = i.lab_id
                WHERE ia.status = 'SUBMITTED'{scope_sql}
                GROUP BY ia.application_id, ia.submitted_at, ia.accept_internal_adjust,
                         ia.accept_cross_adjust, s.student_no, s.name, s.grade, s.major,
                         r.round_name, r.round_no
                ORDER BY ia.submitted_at DESC, ia.application_id DESC
                """,
                params,
            )
            items = cur.fetchall()
            return {"items": items, "total": len(items)}
    finally:
        conn.close()


@app.get("/api/stats/admissions")
def stats_admissions(status: str = "OCCUPIED", user: dict = Depends(current_teacher)):
    """概览“待确认拟录取 / 已确认录取”点击查看：学号、姓名、专业。"""
    if status not in ("OCCUPIED", "CONFIRMED"):
        raise HTTPException(status_code=400, detail="状态仅支持 OCCUPIED 或 CONFIRMED")
    conn = get_conn()
    try:
        with conn.cursor() as cur:
            is_coordinator, scope_labs = teacher_lab_scope(cur, user["teacher_id"])
            scope_sql = ""
            params = [status]
            if not is_coordinator:
                if not scope_labs:
                    return {"items": [], "total": 0}
                placeholders = ",".join(["%s"] * len(scope_labs))
                scope_sql = f" AND a.lab_id IN ({placeholders})"
                params.extend(scope_labs)
            cur.execute(
                f"""
                SELECT s.student_no, s.name AS student_name, s.major,
                       t.name AS mentor_name, l.name AS lab_name
                FROM admission a
                JOIN student s ON s.student_id = a.student_id
                JOIN mentor m ON m.mentor_id = a.mentor_id
                JOIN teacher t ON t.teacher_id = m.teacher_id
                JOIN lab l ON l.lab_id = a.lab_id
                WHERE a.status = %s{scope_sql}
                ORDER BY a.updated_at DESC, a.admission_id DESC
                """,
                params,
            )
            items = cur.fetchall()
            return {"items": items, "total": len(items)}
    finally:
        conn.close()


@app.get("/api/stats/pending-negotiations")
def stats_pending_negotiations(user: dict = Depends(current_teacher)):
    """概览“待约谈”点击查看：姓名、学号、专业、电话。"""
    conn = get_conn()
    try:
        with conn.cursor() as cur:
            is_coordinator, scope_labs = teacher_lab_scope(cur, user["teacher_id"])
            scope_sql = ""
            params = []
            if not is_coordinator:
                if not scope_labs:
                    return {"items": [], "total": 0}
                placeholders = ",".join(["%s"] * len(scope_labs))
                scope_sql = f" AND a.to_lab_id IN ({placeholders})"
                params = list(scope_labs)
            cur.execute(
                f"""
                SELECT s.name AS student_name, s.student_no, s.major, s.phone
                FROM adjustment a
                JOIN student s ON s.student_id = a.student_id
                WHERE a.status = 'PENDING'{scope_sql}
                ORDER BY a.updated_at DESC, a.adjust_id DESC
                """,
                params,
            )
            items = cur.fetchall()
            return {"items": items, "total": len(items)}
    finally:
        conn.close()


@app.post("/api/adjustments/match")
def match_adjustments(body: AdjustMatchIn, user: dict = Depends(current_teacher)):
    """统筹调剂：按 内部调剂 → 跨实训室 顺序匹配，剩余转入约谈（BR-20）。"""
    conn = get_conn()
    try:
        with conn.cursor() as cur:
            require_coordinator(cur, user)
            cur.execute(
                """
                SELECT round_id, round_no, round_type, round_name, fill_start, fill_end
                FROM selection_round WHERE round_id = %s
                """,
                (body.roundId,),
            )
            round_row = cur.fetchone()
            if not round_row:
                raise HTTPException(status_code=404, detail="轮次不存在")
            if round_row["round_type"] != "ADJUST":
                raise HTTPException(status_code=400, detail="仅统筹调剂轮次可执行调剂匹配")

            cur.execute(
                """
                SELECT s.student_id
                FROM student s
                WHERE NOT EXISTS (
                        SELECT 1 FROM admission a
                        WHERE a.student_id = s.student_id
                          AND a.status IN ('OCCUPIED','CONFIRMED','EFFECTIVE','ENROLLED'))
                  AND NOT EXISTS (
                        SELECT 1 FROM adjustment d
                        WHERE d.round_id = %s AND d.student_id = s.student_id)
                ORDER BY s.student_id
                """,
                (body.roundId,),
            )
            student_ids = [r["student_id"] for r in cur.fetchall()]

            items = []
            matched = 0
            pending = 0
            for sid in student_ids:
                cur.execute(
                    """
                    SELECT i.intent_id, i.lab_id, i.mentor_id,
                           ia.accept_internal_adjust, ia.accept_cross_adjust
                    FROM intent i
                    JOIN intent_application ia ON ia.application_id = i.application_id
                    WHERE i.student_id = %s
                    ORDER BY i.round_id DESC, i.parallel_no ASC
                    LIMIT 1
                    """,
                    (sid,),
                )
                origin = cur.fetchone()
                if not origin:
                    # 未填报过有效意向的学生同样纳入统筹调剂，直接转第三级约谈
                    cur.execute(
                        """
                        INSERT INTO adjustment
                          (round_id, student_id, priority_level, adjust_type, from_lab_id,
                           status, operator_id, remark)
                        VALUES (%s, %s, 3, 'NEGOTIATED', NULL, 'PENDING', %s, '无有效意向，转第三级约谈')
                        """,
                        (body.roundId, sid, user["teacher_id"]),
                    )
                    pending += 1
                    items.append({
                        "studentId": sid,
                        "adjustType": "NEGOTIATED",
                        "priorityLevel": 3,
                        "fromLabId": None,
                        "adjustId": cur.lastrowid,
                        "needNegotiation": True,
                    })
                    continue

                adjust_type = None
                to_lab_id = None
                to_mentor_id = None
                priority = None
                if origin["accept_internal_adjust"] and lab_remaining(cur, origin["lab_id"]) > 0:
                    candidate = pick_mentor(cur, origin["lab_id"], origin["mentor_id"])
                    if candidate:
                        priority = 1
                        adjust_type = "INTERNAL"
                        to_lab_id = origin["lab_id"]
                        to_mentor_id = candidate
                if not to_lab_id and origin["accept_cross_adjust"]:
                    cur.execute(
                        "SELECT lab_id FROM lab WHERE status = 'OPEN' AND lab_id <> %s ORDER BY lab_id",
                        (origin["lab_id"],),
                    )
                    for cand in cur.fetchall():
                        if lab_remaining(cur, cand["lab_id"]) > 0:
                            priority = 2
                            adjust_type = "CROSS"
                            to_lab_id = cand["lab_id"]
                            to_mentor_id = pick_mentor(cur, to_lab_id)
                            break

                if to_lab_id and to_mentor_id:
                    created = create_adjust_admission(
                        cur,
                        round_id=body.roundId,
                        lab_id=to_lab_id,
                        student_id=sid,
                        mentor_id=to_mentor_id,
                        intent_id=origin["intent_id"],
                        source_type="ADJUST_INTERNAL" if adjust_type == "INTERNAL" else "ADJUST_CROSS",
                        score=None,
                        operator_teacher_id=user["teacher_id"],
                        operator_account_id=user["account_id"],
                        remark="内部调剂" if adjust_type == "INTERNAL" else "跨实训室调剂",
                    )
                    cur.execute(
                        """
                        INSERT INTO adjustment
                          (round_id, student_id, priority_level, adjust_type, from_lab_id,
                           to_lab_id, to_mentor_id, status, admission_id, operator_id,
                           decided_at, remark)
                        VALUES (%s, %s, %s, %s, %s, %s, %s, 'MATCHED', %s, %s, NOW(3), %s)
                        """,
                        (
                            body.roundId, sid, priority, adjust_type, origin["lab_id"],
                            to_lab_id, to_mentor_id, created["admissionId"],
                            user["teacher_id"], adjust_type,
                        ),
                    )
                    matched += 1
                    items.append({
                        "studentId": sid,
                        "adjustType": adjust_type,
                        "priorityLevel": priority,
                        "toLabId": to_lab_id,
                        "toMentorId": to_mentor_id,
                        "resultId": created["resultId"],
                        "admissionId": created["admissionId"],
                        "needNegotiation": False,
                    })
                else:
                    cur.execute(
                        """
                        INSERT INTO adjustment
                          (round_id, student_id, priority_level, adjust_type, from_lab_id,
                           status, operator_id, remark)
                        VALUES (%s, %s, 3, 'NEGOTIATED', %s, 'PENDING', %s, '无可用调剂名额，转第三级约谈')
                        """,
                        (body.roundId, sid, origin["lab_id"], user["teacher_id"]),
                    )
                    pending += 1
                    items.append({
                        "studentId": sid,
                        "adjustType": "NEGOTIATED",
                        "priorityLevel": 3,
                        "fromLabId": origin["lab_id"],
                        "adjustId": cur.lastrowid,
                        "needNegotiation": True,
                    })
            conn.commit()
            return {
                "roundId": body.roundId,
                "candidates": len(student_ids),
                "matched": matched,
                "pendingNegotiation": pending,
                "items": items,
            }
    except HTTPException:
        conn.rollback()
        raise
    except Exception:
        conn.rollback()
        raise
    finally:
        conn.close()


@app.post("/api/adjustments/{adjust_id}/negotiate")
def negotiate_adjustment(adjust_id: int, body: NegotiateIn,
                         user: dict = Depends(current_teacher)):
    """约谈记录（第三级统筹）：接受则落最终安置，拒绝则记录结论。"""
    conn = get_conn()
    try:
        with conn.cursor() as cur:
            require_coordinator(cur, user)
            if body.conclusion not in ("ACCEPT", "REJECT"):
                raise HTTPException(status_code=400, detail="conclusion 仅支持 ACCEPT/REJECT")
            cur.execute("SELECT * FROM adjustment WHERE adjust_id = %s FOR UPDATE", (adjust_id,))
            adj = cur.fetchone()
            if not adj:
                raise HTTPException(status_code=404, detail="调剂记录不存在")
            if adj["status"] not in ("PENDING", "REJECTED"):
                raise HTTPException(status_code=409, detail="该调剂记录已完成安置，无需再次约谈")

            admission_id = None
            if body.conclusion == "ACCEPT":
                if not body.finalLabId:
                    raise HTTPException(status_code=400, detail="约谈接受须指定最终实训室")
                cur.execute(
                    """
                    SELECT 1 FROM admission WHERE student_id = %s
                      AND status IN ('OCCUPIED','CONFIRMED','EFFECTIVE','ENROLLED')
                    LIMIT 1
                    """,
                    (adj["student_id"],),
                )
                if cur.fetchone():
                    raise HTTPException(status_code=409, detail="该学生已有有效录取")
                mentor_id = body.finalMentorId or pick_mentor(cur, body.finalLabId)
                if not mentor_id:
                    raise HTTPException(status_code=409, detail="该实训室暂无可用导师")
                cur.execute(
                    """
                    SELECT intent_id FROM intent
                    WHERE student_id = %s
                    ORDER BY round_id DESC, parallel_no ASC
                    LIMIT 1
                    """,
                    (adj["student_id"],),
                )
                intent_row = cur.fetchone()
                created = create_adjust_admission(
                    cur,
                    round_id=adj["round_id"],
                    lab_id=body.finalLabId,
                    student_id=adj["student_id"],
                    mentor_id=mentor_id,
                    intent_id=intent_row["intent_id"] if intent_row else None,
                    source_type="NEGOTIATED",
                    score=None,
                    operator_teacher_id=user["teacher_id"],
                    operator_account_id=user["account_id"],
                    remark="约谈统筹安置",
                )
                admission_id = created["admissionId"]
                cur.execute(
                    """
                    UPDATE adjustment
                    SET status = 'MATCHED', to_lab_id = %s, to_mentor_id = %s,
                        admission_id = %s, decided_at = NOW(3), remark = %s
                    WHERE adjust_id = %s
                    """,
                    (body.finalLabId, mentor_id, admission_id, body.remark, adjust_id),
                )
                result_id = created["resultId"]
            else:
                # 拒绝不作为终态：保留待约谈状态，允许继续多轮约谈直至落实实训室
                cur.execute(
                    """
                    UPDATE adjustment
                    SET status = 'PENDING', decided_at = NOW(3), remark = %s
                    WHERE adjust_id = %s
                    """,
                    (body.remark, adjust_id),
                )
                result_id = None

            cur.execute(
                """
                INSERT INTO negotiation
                  (round_id, student_id, interviewer_teacher_id, negotiate_time, method,
                   willingness, conclusion, final_lab_id, final_mentor_id, admission_id, remark)
                VALUES (%s, %s, %s, NOW(3), %s, %s, %s, %s, %s, %s, %s)
                """,
                (
                    adj["round_id"], adj["student_id"], user["teacher_id"], body.method,
                    body.willingness, body.conclusion, body.finalLabId,
                    body.finalMentorId, admission_id, body.remark,
                ),
            )
            negotiation_id = cur.lastrowid
            write_audit(
                cur,
                user["account_id"],
                "TEACHER",
                "ADJUST_NEGOTIATE",
                "NEGOTIATION",
                negotiation_id,
                None,
                {
                    "adjust_id": adjust_id,
                    "student_id": adj["student_id"],
                    "conclusion": body.conclusion,
                    "final_lab_id": body.finalLabId,
                    "admission_id": admission_id,
                },
            )
            conn.commit()
            return {
                "negotiationId": negotiation_id,
                "adjustId": adjust_id,
                "conclusion": body.conclusion,
                "resultId": result_id,
                "admissionId": admission_id,
            }
    except HTTPException:
        conn.rollback()
        raise
    except Exception:
        conn.rollback()
        raise
    finally:
        conn.close()


@app.get("/api/adjustments")
def list_adjustments(roundId: Optional[int] = None,
                     user: dict = Depends(current_teacher)):
    conn = get_conn()
    try:
        with conn.cursor() as cur:
            require_coordinator(cur, user)
            cur.execute(
                """
                SELECT d.adjust_id, d.round_id, d.student_id, s.student_no, s.name AS student_name,
                       d.priority_level, d.adjust_type, d.from_lab_id, d.to_lab_id,
                       d.to_mentor_id, d.status, d.admission_id, d.remark, d.decided_at
                FROM adjustment d
                JOIN student s ON s.student_id = d.student_id
                WHERE (%s IS NULL OR d.round_id = %s)
                ORDER BY d.adjust_id
                """,
                (roundId, roundId),
            )
            return {"items": cur.fetchall()}
    finally:
        conn.close()


WEB_DIR = os.path.join(os.path.dirname(os.path.abspath(__file__)), "web")
if os.path.isdir(WEB_DIR):
    app.mount("/static", StaticFiles(directory=WEB_DIR), name="static")


@app.get("/")
def index():
    return RedirectResponse(url="/static/index.html")


if __name__ == "__main__":
    uvicorn.run(app, host="127.0.0.1", port=int(os.getenv("PORT", "8000")))