"""清理业务数据并生成演示数据。

运行前需启动 API 服务（默认 http://127.0.0.1:8000）：
    python tests/seed_demo.py

生成内容：
- 10 名学生首轮各提交 3 个意向（覆盖 4 个实训室），并补齐个人简历
- 实训室1：场次进行中，3 人已签到并完成双评委评分；李娜已确认录取、张伟待确认
- 实训室2：明日场次已排人，用于演示签到与评分
- 实训室3：场次进行中，孙悦已拟录取、周涛已确认录取，吴敏未签到
- 1 条待约谈调剂记录（王强，数据智能实训室）
- 公示批次 1 个（含拟录取记录）＋ 1 条待处理异议
- 开放日预约 2 条、系统通知若干
"""

import json
import os
import sys
import urllib.error
import urllib.request
from datetime import datetime, timedelta

import pymysql

BASE = os.getenv("API_BASE", "http://127.0.0.1:8000")
DB = {
    "host": os.getenv("DB_HOST", "127.0.0.1"),
    "port": int(os.getenv("DB_PORT", "3306")),
    "user": os.getenv("DB_USER", "root"),
    "password": os.getenv("DB_PASSWORD", "050613ying"),
    "database": os.getenv("DB_NAME", "sxsxb"),
    "charset": "utf8mb4",
    "cursorclass": pymysql.cursors.DictCursor,
    "autocommit": True,
}

if hasattr(sys.stdout, "reconfigure"):
    sys.stdout.reconfigure(encoding="utf-8")


def api(method, path, token=None, body=None):
    data = json.dumps(body, ensure_ascii=False).encode("utf-8") if body is not None else None
    req = urllib.request.Request(BASE + path, data=data, method=method)
    req.add_header("Content-Type", "application/json; charset=utf-8")
    if token:
        req.add_header("Authorization", "Bearer " + token)
    try:
        with urllib.request.urlopen(req, timeout=20) as resp:
            return resp.status, json.loads(resp.read().decode("utf-8"))
    except urllib.error.HTTPError as err:
        raw = err.read().decode("utf-8")
        try:
            return err.code, json.loads(raw)
        except json.JSONDecodeError:
            return err.code, {"raw": raw}


def login(name, password="123456"):
    status, data = api("POST", "/api/auth/login", body={"loginName": name, "password": password})
    if status != 200:
        raise RuntimeError(f"登录失败 {name}: {status} {data}")
    return data["token"]


def db():
    return pymysql.connect(**DB)


def clean():
    conn = db()
    with conn.cursor() as cur:
        for table in (
            "objection", "publicity_item", "publicity_batch", "open_day_reservation",
            "notification", "interview_reminder", "interview_score", "interview_participant",
            "interview_judge", "interview_session", "lab_quota_log", "negotiation",
            "adjustment", "admission", "selection_result", "audit_log", "intent",
            "intent_application",
        ):
            cur.execute(f"DELETE FROM {table}")
        cur.execute("UPDATE student SET selection_state = 'NOT_FILLED'")
        cur.execute("UPDATE lab SET quota = 12 WHERE lab_id IN (1,2,3,4)")
        cur.execute("UPDATE lab SET status = 'OPEN' WHERE lab_id IN (1,2,3,4)")
        cur.execute(
            "UPDATE selection_round SET fill_start='2026-11-10 00:00:00', "
            "fill_end='2026-11-30 23:59:59', interview_start='2026-11-12 08:00:00', "
            "interview_end='2026-11-25 18:00:00', confirm_start='2026-11-12 08:00:00', "
            "confirm_end='2026-11-30 23:59:59', status='FILLING' WHERE round_id=1"
        )
        cur.execute(
            "UPDATE selection_round SET fill_start='2026-12-01 09:00:00', "
            "fill_end='2026-12-01 17:00:00', interview_start='2026-12-02 09:00:00', "
            "interview_end='2026-12-02 17:00:00', status='PLANNED' WHERE round_id=2"
        )
        cur.execute(
            "UPDATE selection_round SET fill_start='2026-12-03 09:00:00', "
            "fill_end='2026-12-03 17:00:00', interview_start='2026-12-04 09:00:00', "
            "interview_end='2026-12-04 17:00:00', status='PLANNED' WHERE round_id=3"
        )
        cur.execute(
            "UPDATE selection_round SET fill_start='2026-12-05 09:00:00', "
            "fill_end='2026-12-05 17:00:00', interview_start='2026-12-05 09:00:00', "
            "interview_end='2026-12-05 17:00:00', status='PLANNED' WHERE round_id=4"
        )
    conn.close()


def fill_resumes():
    """补齐演示学生个人简历（年级排名、获奖、擅长方向、意向发展方向、电话）。"""
    rows = [
        ("S2026001", "8/126", "蓝桥杯省赛二等奖；校级三好学生", "Python、Web 前端开发", "AI 应用开发", "13803110001"),
        ("S2026002", "12/126", "全国大学生数学建模竞赛省二等奖", "机器学习、数据分析", "数据智能方向", "13803110002"),
        ("S2026003", "25/124", "校级程序设计竞赛一等奖", "Java 后端、数据库设计", "全栈开发", "13803110003"),
        ("S2026004", "5/124", "互联网+省级铜奖", "算法与数据结构、Go 后端", "智能硬件与嵌入式", "13803110004"),
        ("S2026005", "18/122", "校级优秀学生干部", "Vue、UI 设计", "前端与交互设计", "13803110005"),
        ("S2026006", "33/126", "蓝桥杯校赛三等奖", "C++、Linux 运维", "系统与运维", "13803110006"),
        ("S2026007", "7/124", "全国大学生数学建模竞赛省一等奖", "Python、数据可视化", "数据智能方向", "13803110007"),
        ("S2026008", "21/125", "校级网络技术大赛二等奖", "网络协议、网络安全", "网络与安全", "13803110008"),
        ("S2026009", "15/123", "校级奖学金二等奖", "SQL、报表开发", "数据分析", "13803110009"),
        ("S2026010", "29/126", "大学生创新创业训练计划校级立项", "Java、Spring Boot", "后端开发", "13803110010"),
    ]
    conn = db()
    with conn.cursor() as cur:
        for no, rank, awards, specialty, dev, phone in rows:
            cur.execute(
                """
                UPDATE student SET grade_rank=%s, awards=%s, specialty=%s,
                       dev_direction=%s, phone=%s
                WHERE student_no=%s
                """,
                (rank, awards, specialty, dev, phone, no),
            )
    conn.close()


def file_application(token, mentor_ids):
    status, app = api("POST", "/api/filing/applications", token,
                      {"acceptInternalAdjust": True, "acceptCrossAdjust": True})
    if status != 200:
        raise RuntimeError(f"创建申请单失败 {status} {app}")
    status, data = api("PUT", f"/api/filing/applications/{app['applicationId']}/intents", token,
                       {"mentorIds": mentor_ids, "acceptInternalAdjust": True,
                        "acceptCrossAdjust": True})
    if status != 200:
        raise RuntimeError(f"保存意向失败 {status} {data}")
    status, sub = api("POST", f"/api/filing/applications/{app['applicationId']}/submit", token, {})
    if status != 200:
        raise RuntimeError(f"提交意向失败 {status} {sub}")
    return {row["mentor_id"]: row["intent_id"] for row in data["intents"]}


def make_session(token, lab_id, name, start, end, location, judges):
    status, data = api("POST", "/api/interview/sessions", token, {
        "roundId": 1, "labId": lab_id, "sessionName": name,
        "startTime": start, "endTime": end, "location": location,
        "judgeTeacherIds": judges,
    })
    if status != 200:
        raise RuntimeError(f"创建场次失败 {status} {data}")
    sid = data["sessionId"]
    status, data = api("POST", f"/api/interview/sessions/{sid}/participants", token, {"auto": True})
    if status != 200:
        raise RuntimeError(f"排入学生失败 {status} {data}")
    return sid


def set_interviewing(session_id):
    conn = db()
    with conn.cursor() as cur:
        cur.execute(
            "UPDATE interview_session SET start_time=DATE_SUB(NOW(3), INTERVAL 1 HOUR), "
            "end_time=DATE_ADD(NOW(3), INTERVAL 1 HOUR), status='INTERVIEWING' WHERE session_id=%s",
            (session_id,),
        )
    conn.close()


def checkin(token, session_id, student_id):
    status, data = api("POST", f"/api/interview/sessions/{session_id}/checkin", token,
                       {"studentId": student_id, "status": "CHECKED_IN"})
    if status != 200:
        raise RuntimeError(f"签到失败 {status} {data}")


def score(token, session_id, student_id, values, comment):
    status, data = api("POST", f"/api/interview/sessions/{session_id}/scores", token, {
        "studentId": student_id, "learningScore": values[0], "logicScore": values[1],
        "programmingScore": values[2], "timeScore": values[3], "comment": comment,
    })
    if status != 200:
        raise RuntimeError(f"评分失败 {status} {data}")
    return data.get("totalScore")


def main():
    try:
        urllib.request.urlopen(BASE + "/api/health", timeout=5).read()
    except Exception as exc:
        print(f"服务不可用，请先启动 API：{exc}")
        return 1

    clean()
    fill_resumes()
    t = {name: login(name) for name in
         ("T001", "T002", "T003", "T004", "T005", "T006", "T007", "T009")}
    s = {i: login(f"S2026{i:03d}") for i in range(1, 11)}

    # ---------- 意向填报 ----------
    mapping = {
        1: [1, 4, 7], 2: [2, 5, 8], 3: [3, 6, 9], 4: [4, 1, 10], 5: [5, 2, 11],
        6: [6, 3, 12], 7: [7, 10, 1], 8: [8, 11, 2], 9: [9, 12, 3], 10: [10, 7, 4],
    }
    intents = {}
    for sid, mentors in mapping.items():
        intents[sid] = file_application(s[sid], mentors)
    print(f"已生成 10 份申请单，共 {sum(len(v) for v in intents.values())} 条意向")

    # ---------- 实训室1：已面试、已评分（评委默认=学生所选导师） ----------
    s1 = make_session(t["T001"], 1, "AI 应用实训室-首轮面试（上午场）",
                      "2026-11-12 09:00:00", "2026-11-12 11:00:00", "实训楼 A305", [])
    set_interviewing(s1)
    for sid in (1, 2, 3):
        checkin(t["T001"], s1, sid)
    score(t["T001"], s1, 1, (80, 82, 84, 78), "学习态度端正，表达清晰")
    for sid, values in ((2, (84, 86, 88, 82)), (3, (88, 90, 92, 86))):
        score(t["T001"], s1, sid, tuple(v - 4 for v in values), "负责人复核评分")
        score(t["T005"] if sid == 2 else t["T006"], s1, sid, values, "所选导师评分")
    print(f"实训室1 场次 {s1}：签到 3 人、导师评分完成")

    # ---------- 实训室2：明日场次，待现场签到评分 ----------
    tomorrow = (datetime.now() + timedelta(days=1)).strftime("%Y-%m-%d")
    s2 = make_session(t["T002"], 2, "智能硬件实训室-首轮面试（明日场）",
                      f"{tomorrow} 09:00:00", f"{tomorrow} 11:00:00", "实训楼 B201", [])
    print(f"实训室2 场次 {s2}：已排入学生，状态 PLANNED")

    # ---------- 实训室3：已面试、部分拟录取 ----------
    s3 = make_session(t["T003"], 3, "数据智能实训室-首轮面试（下午场）",
                      "2026-11-12 14:00:00", "2026-11-12 16:00:00", "实训楼 C102", [])
    set_interviewing(s3)
    for sid in (7, 8):
        checkin(t["T003"], s3, sid)
    score(t["T003"], s3, 7, (88, 90, 92, 86), "逻辑清晰，数据敏感度高")
    score(t["T003"], s3, 8, (82, 84, 86, 80), "负责人复核评分")
    score(t["T009"], s3, 8, (80, 82, 84, 78), "所选导师评分")
    print(f"实训室3 场次 {s3}：孙悦/周涛已签到评分，吴敏待签到")

    # ---------- 拟录取 ----------
    def propose(intent_id, remark):
        status, data = api("POST", "/api/results/propose", t["T001"], {"intentId": intent_id, "remark": remark})
        if status != 200:
            raise RuntimeError(f"拟录取失败 {status} {data}")
        return data

    r_zhang = propose(intents[1][1], "综合评分靠前，拟录取")
    r_li = propose(intents[2][2], "面试表现优秀，拟录取")
    r_sun = propose(intents[7][7], "数据方向匹配度高，拟录取")
    r_zhou = propose(intents[8][8], "评分达标，拟录取")
    for token, result, who in ((s[2], r_li, "李娜"), (s[8], r_zhou, "周涛")):
        status, data = api("POST", f"/api/results/{result['resultId']}/confirm", token, {})
        if status != 200:
            raise RuntimeError(f"{who} 确认失败 {status} {data}")
    print("拟录取：张伟（待确认）、李娜（已确认）、孙悦（待确认）、周涛（已确认）")

    # ---------- 待约谈调剂 ----------
    conn = db()
    with conn.cursor() as cur:
        cur.execute(
            """
            INSERT INTO adjustment
              (round_id, student_id, priority_level, adjust_type, from_lab_id,
               to_lab_id, to_mentor_id, status, operator_id, remark)
            VALUES (4, 3, 3, 'NEGOTIATED', NULL, 2, NULL, 'PENDING', 0,
                    '首轮未获拟录取，安排统筹约谈')
            """
        )
    conn.close()
    print("待约谈：王强 → 智能硬件实训室")

    # ---------- 公示与异议 ----------
    status, batch = api("POST", "/api/publicity/batches", t["T001"], {
        "roundId": 1,
        "startTime": datetime.now().strftime("%Y-%m-%d %H:%M:%S"),
        "endTime": (datetime.now() + timedelta(days=1)).strftime("%Y-%m-%d %H:%M:%S"),
        "remark": "首轮拟录取名单公示",
    })
    if status != 200:
        raise RuntimeError(f"发布公示失败 {status} {batch}")
    batch_id = batch["batchId"]
    status, detail = api("GET", f"/api/publicity/batches/{batch_id}", s[1])
    item = (detail.get("items") or [{}])[0]
    status, obj = api("POST", "/api/objections", s[1], {
        "batchId": batch_id,
        "publicityItemId": item.get("item_id"),
        "content": "希望公示页面补充面试各维度得分与排名依据，便于核对。",
    })
    print(f"公示批次 {batch_id} 已发布，异议提交 -> {status}")

    # ---------- 开放日预约 ----------
    visit = (datetime.now() + timedelta(days=2)).strftime("%Y-%m-%d")
    for sid, lab, slot, remark in ((1, 1, "上午", "想了解实验室项目与设备"),
                                   (4, 2, "下午", "关注智能硬件方向")):
        status, data = api("POST", "/api/open-days/reservations", s[sid], {
            "labId": lab, "visitDate": visit, "timeSlot": slot, "remark": remark,
        })
        if status != 200:
            raise RuntimeError(f"开放日预约失败 {status} {data}")
    print(f"开放日预约：张伟（AI 应用）、刘洋（智能硬件） {visit}")

    # ---------- 汇总 ----------
    conn = db()
    with conn.cursor() as cur:
        cur.execute("SELECT COUNT(*) AS c FROM intent")
        ic = cur.fetchone()["c"]
        cur.execute("SELECT COUNT(*) AS c FROM admission WHERE status='OCCUPIED'")
        occ = cur.fetchone()["c"]
        cur.execute("SELECT COUNT(*) AS c FROM notification")
        nc = cur.fetchone()["c"]
    conn.close()
    print(f"演示数据完成：意向 {ic} 条，待确认拟录取 {occ} 条，通知 {nc} 条")
    return 0


if __name__ == "__main__":
    sys.exit(main())
