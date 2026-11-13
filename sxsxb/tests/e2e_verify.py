"""实训室选报系统 业务链路自动化回归脚本。

覆盖：填报 → 排期 → 签到 → 评分 → 拟录取 → 确认，
以及本阶段优化项：学生放弃拟录取、确认超时自动释放、导师均衡分配、补录资格拦截。

运行前需启动 API 服务（默认 http://127.0.0.1:8000）：
    python tests/e2e_verify.py
"""

import json
import os
import sys
import urllib.error
import urllib.request

import pymysql

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if ROOT not in sys.path:
    sys.path.insert(0, ROOT)

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

RESULTS = []


def check(name, ok, detail=""):
    RESULTS.append((name, ok, detail))
    print(f"[{'PASS' if ok else 'FAIL'}] {name}{(' -> ' + detail) if detail else ''}")


def api(method, path, token=None, body=None):
    data = json.dumps(body, ensure_ascii=False).encode("utf-8") if body is not None else None
    req = urllib.request.Request(BASE + path, data=data, method=method)
    req.add_header("Content-Type", "application/json; charset=utf-8")
    if token:
        req.add_header("Authorization", "Bearer " + token)
    try:
        with urllib.request.urlopen(req, timeout=15) as resp:
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
            "notification", "interview_reminder", "interview_score", "interview_participant",
            "interview_judge", "interview_session", "lab_quota_log", "negotiation",
            "adjustment", "admission", "selection_result", "audit_log", "intent",
            "intent_application",
        ):
            cur.execute(f"DELETE FROM {table}")
        cur.execute("UPDATE student SET selection_state = 'NOT_FILLED'")
        cur.execute("UPDATE lab SET quota = 12 WHERE lab_id IN (1,2,3,4)")
        cur.execute(
            "UPDATE selection_round SET fill_start='2026-11-09 00:00:00', "
            "fill_end='2026-11-11 23:59:59', interview_start='2026-11-12 08:00:00', "
            "interview_end='2026-11-13 17:00:00', status='FILLING' WHERE round_id=1"
        )
        cur.execute(
            "UPDATE selection_round SET fill_start='2026-11-14 12:00:00', "
            "fill_end='2026-11-14 15:00:00', interview_start='2026-11-14 15:00:00', "
            "interview_end='2026-11-14 21:00:00', status='PLANNED' WHERE round_id=2"
        )
        cur.execute(
            "UPDATE selection_round SET fill_start='2026-11-15 11:00:00', "
            "fill_end='2026-11-15 13:00:00', interview_start='2026-11-15 13:00:00', "
            "interview_end='2026-11-15 15:00:00', status='PLANNED' WHERE round_id=3"
        )
        cur.execute(
            "UPDATE selection_round SET fill_start='2026-11-16 08:00:00', "
            "fill_end='2026-11-16 12:00:00', interview_start='2026-11-16 08:00:00', "
            "interview_end='2026-11-16 12:00:00', status='PLANNED' WHERE round_id=4"
        )
    conn.close()


def file_application(token, mentor_ids):
    status, app = api("POST", "/api/filing/applications", token,
                      {"acceptInternalAdjust": True, "acceptCrossAdjust": True})
    assert status == 200, app
    status, intents = api("PUT", f"/api/filing/applications/{app['applicationId']}/intents", token,
                          {"mentorIds": mentor_ids, "acceptInternalAdjust": True,
                           "acceptCrossAdjust": True})
    assert status == 200, intents
    status, sub = api("POST", f"/api/filing/applications/{app['applicationId']}/submit", token, {})
    assert status == 200 and sub["status"] == "SUBMITTED", sub
    return app["applicationId"], intents["intents"]


def main():
    # ---------- 准备 ----------
    try:
        with urllib.request.urlopen(BASE + "/docs", timeout=5) as resp:
            check("服务可用", resp.status == 200, f"HTTP {resp.status}")
    except Exception as exc:
        print(f"服务不可用，终止：{exc}")
        return 1

    clean()
    t1 = login("T001")
    t5 = login("T005")
    students = {"A": "S2026001", "B": "S2026002", "C": "S2026003"}
    tokens = {k: login(v) for k, v in students.items()}

    # ---------- 填报 ----------
    intents = {}
    for key, token in tokens.items():
        app_id, rows = file_application(token, [1])
        intents[key] = rows[0]["intent_id"]
        check(f"学生{key} 填报并提交", bool(rows), f"申请单 {app_id} 意向 {rows[0]['intent_id']}")

    # ---------- 排期 ----------
    status, session = api("POST", "/api/interview/sessions", t1, {
        "roundId": 1, "labId": 1, "sessionName": "回归测试-首轮面试",
        "startTime": "2026-11-12 09:00:00", "endTime": "2026-11-12 11:00:00",
        "location": "实训楼A305", "judgeTeacherIds": [1, 5],
    })
    check("创建面试场次", status == 200, f"session {session.get('sessionId')} {session.get('detail', '')}")
    sid = session["sessionId"]

    status, data = api("POST", f"/api/interview/sessions/{sid}/participants", t1, {"auto": True})
    check("自动排入候选人", status == 200, f"added={data.get('added')}")

    conn = db()
    with conn.cursor() as cur:
        cur.execute(
            "UPDATE interview_session SET start_time=DATE_SUB(NOW(3), INTERVAL 2 HOUR), "
            "end_time=DATE_ADD(NOW(3), INTERVAL 1 HOUR), status='INTERVIEWING' WHERE session_id=%s",
            (sid,),
        )
    conn.close()

    # ---------- 签到 ----------
    for key, stu_id in (("A", 1), ("B", 2), ("C", 3)):
        status, data = api("POST", f"/api/interview/sessions/{sid}/checkin", t1,
                           {"studentId": stu_id, "status": "CHECKED_IN"})
        check(f"学生{key} 签到", status == 200 and data.get("checkinStatus") == "CHECKED_IN")

    # ---------- 评分 ----------
    scores = {"A": ((80, 82, 84, 78), (78, 80, 82, 76)),
              "B": ((86, 88, 90, 84), (84, 86, 88, 82)),
              "C": ((90, 92, 94, 88), (88, 90, 92, 86))}
    for key, stu_id in (("A", 1), ("B", 2), ("C", 3)):
        for judge, values in ((t1, scores[key][0]), (t5, scores[key][1])):
            status, data = api("POST", f"/api/interview/sessions/{sid}/scores", judge, {
                "studentId": stu_id, "learningScore": values[0], "logicScore": values[1],
                "programmingScore": values[2], "timeScore": values[3], "comment": "回归测试评分",
            })
            check(f"学生{key} 评分合计 {data.get('totalScore')}", status == 200)

    # ---------- 拟录取 ----------
    results = {}
    for key in ("A", "B", "C"):
        status, data = api("POST", "/api/results/propose", t1,
                           {"intentId": intents[key], "remark": "回归测试拟录取"})
        results[key] = data
        check(f"学生{key} 拟录取", status == 200, f"result {data.get('resultId')}")

    # ---------- C：确认 ----------
    status, data = api("POST", f"/api/results/{results['C']['resultId']}/confirm", tokens["C"], {})
    check("学生C 确认录取", status == 200 and data.get("status") == "CONFIRMED",
          str(data.get("status")))
    conn = db()
    with conn.cursor() as cur:
        cur.execute("SELECT selection_state FROM student WHERE student_id=3")
        state = cur.fetchone()["selection_state"]
    conn.close()
    check("学生C 状态为 CONFIRMED", state == "CONFIRMED", state)

    # ---------- A：放弃 ----------
    status, data = api("POST", f"/api/results/{results['A']['resultId']}/abandon", tokens["A"],
                       {"reason": "回归测试-学生主动放弃"})
    check("学生A 放弃拟录取", status == 200 and data.get("status") == "ABANDONED")
    conn = db()
    with conn.cursor() as cur:
        cur.execute("SELECT status FROM admission WHERE admission_id=%s", (results["A"]["admissionId"],))
        adm_status = cur.fetchone()["status"]
        cur.execute("SELECT change_type, delta FROM lab_quota_log WHERE admission_id=%s ORDER BY log_id",
                    (results["A"]["admissionId"],))
        logs = cur.fetchall()
    conn.close()
    check("放弃后录取状态 ABANDONED", adm_status == "ABANDONED", adm_status)
    check("放弃后写释放流水", any(r["change_type"] == "RELEASE" and r["delta"] == -1 for r in logs),
          str(logs))

    status, data = api("POST", f"/api/results/{results['C']['resultId']}/abandon", tokens["C"], {})
    check("已确认学生不可放弃", status == 409, f"HTTP {status}")

    # ---------- B：确认超时自动释放 ----------
    conn = db()
    with conn.cursor() as cur:
        cur.execute("UPDATE admission SET confirm_deadline=DATE_SUB(NOW(3), INTERVAL 1 MINUTE) "
                    "WHERE admission_id=%s", (results["B"]["admissionId"],))
    conn.close()
    status, data = api("POST", "/api/results/release-expired", t1, {})
    check("触发超时释放接口", status == 200, json.dumps(data, ensure_ascii=False))
    conn = db()
    with conn.cursor() as cur:
        cur.execute("SELECT status FROM admission WHERE admission_id=%s", (results["B"]["admissionId"],))
        adm_status = cur.fetchone()["status"]
        cur.execute("SELECT COUNT(*) AS c FROM notification WHERE biz_id=%s AND title LIKE '%%超时%%'",
                    (results["B"]["admissionId"],))
        notice = cur.fetchone()["c"]
    conn.close()
    check("超时后录取状态 RELEASED", adm_status == "RELEASED", adm_status)
    check("超时释放生成通知", notice >= 1, f"{notice} 条")

    # ---------- 补录资格拦截 ----------
    conn = db()
    with conn.cursor() as cur:
        cur.execute("UPDATE selection_round SET fill_end=DATE_SUB(NOW(3), INTERVAL 1 MINUTE) "
                    "WHERE round_id=1")
        cur.execute("UPDATE selection_round SET fill_start=DATE_SUB(NOW(3), INTERVAL 2 MINUTE), "
                    "fill_end=DATE_ADD(NOW(3), INTERVAL 5 MINUTE), status='FILLING' WHERE round_id=2")
    conn.close()
    status, data = api("POST", "/api/filing/applications", tokens["C"],
                       {"acceptInternalAdjust": True, "acceptCrossAdjust": True})
    check("已录取学生禁止补录", status == 409 and "不能参加补录" in str(data.get("detail")),
          f"HTTP {status} {data.get('detail')}")
    conn = db()
    with conn.cursor() as cur:
        cur.execute("UPDATE selection_round SET fill_start='2026-11-09 00:00:00', "
                    "fill_end='2026-11-11 23:59:59', status='FILLING' WHERE round_id=1")
        cur.execute("UPDATE selection_round SET fill_start='2026-11-14 12:00:00', "
                    "fill_end='2026-11-14 15:00:00', status='PLANNED' WHERE round_id=2")
    conn.close()

    # ---------- 导师均衡分配 ----------
    import filing_api
    conn = db()
    with conn.cursor() as cur:
        cur.execute("SELECT lab_id, COUNT(*) AS c FROM mentor WHERE status='ACTIVE' "
                    "GROUP BY lab_id HAVING c>=2 ORDER BY lab_id LIMIT 1")
        lab = cur.fetchone()
        if lab:
            cur.execute("SELECT mentor_id FROM mentor WHERE lab_id=%s AND status='ACTIVE' "
                        "ORDER BY mentor_id", (lab["lab_id"],))
            mentors = [r["mentor_id"] for r in cur.fetchall()]
            first = mentors[0]
            cur.execute(
                "INSERT INTO admission (student_id, lab_id, mentor_id, round_id, source_type, "
                "confirm_type, status) VALUES (9999, %s, %s, 1, 'ROUND1', 'MANUAL', 'OCCUPIED')",
                (lab["lab_id"], first),
            )
            picked = filing_api.pick_mentor(cur, lab["lab_id"])
            cur.execute("DELETE FROM admission WHERE student_id=9999")
            check("导师均衡分配避开高负载导师", picked != first,
                  f"lab {lab['lab_id']} 已占 {first}，选择 {picked}")
        else:
            check("导师均衡分配", True, "无多导师实训室，跳过")
    conn.close()

    # ---------- 收尾 ----------
    clean()
    conn = db()
    with conn.cursor() as cur:
        cur.execute(
            "SELECT (SELECT COUNT(*) FROM admission) a, (SELECT COUNT(*) FROM selection_result) r, "
            "(SELECT COUNT(*) FROM interview_session) s, (SELECT COUNT(*) FROM intent) i, "
            "(SELECT COUNT(*) FROM audit_log) l"
        )
        counts = cur.fetchone()
    conn.close()
    check("测试数据已清理", all(v == 0 for v in counts.values()), str(counts))

    failed = [r for r in RESULTS if not r[1]]
    print(f"\n合计 {len(RESULTS)} 项，通过 {len(RESULTS) - len(failed)} 项，失败 {len(failed)} 项")
    return 1 if failed else 0


if __name__ == "__main__":
    sys.exit(main())
