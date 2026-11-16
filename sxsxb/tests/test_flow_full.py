"""依照选拔流程图执行端到端验证。

覆盖链路（按流程图顺序）：
  集中宣讲 → 实训室开放日 → 意向填报(首轮选报) → 导师初选与面试考核
  → 拟录取与学生确认 → 分轮补录(第二轮/第三轮) → 统筹调剂录取 → 结果公示与异议

运行前需启动 API 服务：
    $env:DB_PASSWORD='...'
    python tests/test_flow_full.py

脚本会先清空业务数据跑完整链路，结束后重新生成演示数据。
"""

import json
import os
import sys
import urllib.error
import urllib.request
from datetime import datetime, timedelta

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import pymysql  # noqa: E402

import seed_demo  # noqa: E402

if hasattr(sys.stdout, "reconfigure"):
    sys.stdout.reconfigure(encoding="utf-8")

BASE = os.getenv("API_BASE", "http://127.0.0.1:8000")
DB = dict(host=os.getenv("DB_HOST", "127.0.0.1"), port=int(os.getenv("DB_PORT", "3306")),
          user=os.getenv("DB_USER", "root"), password=os.getenv("DB_PASSWORD", "050613ying"),
          database=os.getenv("DB_NAME", "sxsxb"), charset="utf8mb4",
          cursorclass=pymysql.cursors.DictCursor, autocommit=True)

PASSED, FAILED = [], []


def api(method, path, token=None, body=None):
    data = json.dumps(body, ensure_ascii=False).encode("utf-8") if body is not None else (
        b"" if method == "POST" else None)
    req = urllib.request.Request(BASE + path, data=data, method=method)
    req.add_header("Content-Type", "application/json; charset=utf-8")
    if token:
        req.add_header("Authorization", "Bearer " + token)
    try:
        with urllib.request.urlopen(req, timeout=30) as resp:
            return resp.status, json.loads(resp.read().decode("utf-8"))
    except urllib.error.HTTPError as err:
        raw = err.read().decode("utf-8")
        try:
            return err.code, json.loads(raw)
        except json.JSONDecodeError:
            return err.code, {"raw": raw}


def db():
    return pymysql.connect(**DB)


def q(sql, args=(), one=False):
    conn = db()
    try:
        with conn.cursor() as cur:
            cur.execute(sql, args)
            rows = cur.fetchall()
    finally:
        conn.close()
    return (rows[0] if rows else None) if one else rows


def x(sql, args=()):
    conn = db()
    try:
        with conn.cursor() as cur:
            cur.execute(sql, args)
            return cur.rowcount
    finally:
        conn.close()


def check(cond, label, detail=""):
    if cond:
        PASSED.append(label)
        print(f"  [PASS] {label}")
    else:
        FAILED.append(f"{label} {detail}")
        print(f"  [FAIL] {label} {detail}")


def stage(code):
    return q("SELECT * FROM selection_stage WHERE stage_code=%s", (code,), one=True)


def rnd(no):
    return q("SELECT * FROM selection_round WHERE round_no=%s", (no,), one=True)


def login(name):
    status, data = api("POST", "/api/auth/login", body={"loginName": name, "password": "123456"})
    assert status == 200, f"登录失败 {name}: {data}"
    return data["token"]


def prepare():
    seed_demo.clean()
    x("UPDATE selection_stage SET status='CLOSED', opened_at=NULL, closed_at=NULL")
    x("UPDATE selection_round SET status='CLOSED'")
    x("UPDATE selection_round SET status='PLANNED' WHERE round_id IN (1,2,3,4)")


def restore_demo():
    seed_demo.clean()
    seed_demo.main()
    x("UPDATE selection_stage SET status='CLOSED' WHERE stage_no <= 7")
    x("UPDATE selection_stage SET status='PLANNED' WHERE stage_code='PUBLICITY'")


def main():
    prepare()
    t = login("T001")
    s1, s2, s3 = login("S2026001"), login("S2026002"), login("S2026003")
    st = {r["stage_code"]: r for r in q("SELECT * FROM selection_stage")}

    lab = q("SELECT lab_id, name FROM lab WHERE status='OPEN' ORDER BY lab_id LIMIT 1", one=True)
    mentors = q("SELECT mentor_id, teacher_id FROM mentor WHERE lab_id=%s AND status='ACTIVE' "
                "ORDER BY mentor_id", (lab["lab_id"],))
    assert len(mentors) >= 2, "演示数据导师不足"
    mentor_ids = [m["mentor_id"] for m in mentors]
    judge_ids = [m["teacher_id"] for m in mentors[:1]]

    print("\n== 阶段 1 集中宣讲 ==")
    code, data = api("POST", f"/api/stages/{st['BRIEFING']['stage_id']}/open", t)
    check(code == 200 and stage("BRIEFING")["status"] == "OPEN", "开启集中宣讲阶段")
    check(not data.get("linkedRound"), "集中宣讲不联动任何轮次", str(data))

    print("\n== 阶段 2 实训室开放日 ==")
    code, _ = api("POST", f"/api/stages/{st['OPEN_DAY']['stage_id']}/open", t)
    check(code == 200 and stage("OPEN_DAY")["status"] == "OPEN", "开启开放日阶段")
    check(stage("BRIEFING")["status"] == "CLOSED", "同时间仅一个阶段进行中（宣讲自动结束）")
    visit = (datetime.now() + timedelta(days=1)).strftime("%Y-%m-%d")
    code, r1 = api("POST", "/api/open-days/reservations", s1,
                   {"labId": lab["lab_id"], "visitDate": visit, "timeSlot": "MORNING", "remark": "验证"})
    check(code == 200, "学生在开放日阶段预约参观", str(r1))
    api("POST", f"/api/open-days/reservations/{r1['reservationId']}/checkin", t)
    code, r2 = api("POST", "/api/open-days/reservations", s2,
                   {"labId": lab["lab_id"], "visitDate": visit, "timeSlot": "AFTERNOON", "remark": "验证"})
    check(code == 200, "第二名学生预约参观", str(r2))
    code, _ = api("POST", f"/api/stages/{st['OPEN_DAY']['stage_id']}/close", t)
    rows = q("SELECT status FROM open_day_reservation")
    check(code == 200 and all(r["status"] == "CHECKED_IN" for r in rows) and len(rows) == 1,
          "关闭开放日阶段清空未签到预约，仅保留已签到", str(rows))

    print("\n== 阶段 3 意向填报（首轮选报） ==")
    code, data = api("POST", f"/api/stages/{st['INTENT']['stage_id']}/open", t)
    check(code == 200 and stage("INTENT")["status"] == "OPEN", "开启意向填报阶段")
    check(rnd(1)["status"] == "FILLING", "首轮选报同步开放填报", str(rnd(1)))
    check(bool(data.get("linkedRound")), "返回关联轮次", str(data))
    apps = {}
    for token, key in ((s1, "s1"), (s2, "s2"), (s3, "s3")):
        code, data = api("POST", "/api/filing/applications", token)
        check(code == 200, f"创建意向单 {key}", str(data))
        apps[key] = data["applicationId"]
    code, data = api("PUT", f"/api/filing/applications/{apps['s1']}/intents", s1,
                     {"mentorIds": mentor_ids[:3], "acceptInternalAdjust": True,
                      "acceptCrossAdjust": True})
    check(code == 200, "保存 3 个平行意向", str(data))
    code, data = api("PUT", f"/api/filing/applications/{apps['s1']}/intents", s1,
                     {"mentorIds": mentor_ids[:3] + [mentor_ids[0]]})
    check(code == 400 or code == 422, "平行意向重复被拒绝", str(data))
    code, data = api("POST", f"/api/filing/applications/{apps['s1']}/submit", s1)
    check(code == 200, "提交首轮意向", str(data))
    for token, key in ((s2, "s2"), (s3, "s3")):
        api("PUT", f"/api/filing/applications/{apps[key]}/intents", token,
            {"mentorIds": mentor_ids[:2]})
        code, _ = api("POST", f"/api/filing/applications/{apps[key]}/submit", token)
        check(code == 200, f"提交首轮意向 {key}")
    code, _ = api("POST", f"/api/filing/applications/{apps['s1']}/submit", s1)
    check(code == 409, "已提交意向不可重复提交")

    print("\n== 阶段 4 导师初选与面试考核 ==")
    api("POST", f"/api/stages/{st['INTERVIEW']['stage_id']}/open", t)
    check(stage("INTERVIEW")["status"] == "OPEN", "开启面试阶段")
    start = (datetime.now() - timedelta(hours=1)).strftime("%Y-%m-%dT%H:%M:%S")
    end = (datetime.now() + timedelta(hours=2)).strftime("%Y-%m-%dT%H:%M:%S")
    code, sess = api("POST", "/api/interview/sessions", t,
                     {"roundId": rnd(1)["round_id"], "labId": lab["lab_id"],
                      "sessionName": "流程验证场次", "startTime": start, "endTime": end,
                      "location": "实训楼 101", "judgeTeacherIds": judge_ids})
    check(code == 200, "创建面试场次", str(sess))
    sid = sess.get("sessionId") or sess.get("session_id")
    code, plan = api("POST", f"/api/interview/sessions/{sid}/participants", t, {"auto": True})
    check(code == 200, "自动排入本室意向学生", str(plan))
    participants = q("SELECT student_id, intent_id FROM interview_participant WHERE session_id=%s", (sid,))
    check(len(participants) >= 3, "场次内学生数符合预期", str(len(participants)))
    for p in participants:
        api("POST", f"/api/interview/sessions/{sid}/checkin", t,
            {"studentId": p["student_id"], "intentId": p["intent_id"], "status": "CHECKED_IN"})
        api("POST", f"/api/interview/sessions/{sid}/scores", t,
            {"studentId": p["student_id"], "intentId": p["intent_id"],
             "learningScore": 90, "logicScore": 90, "programmingScore": 90,
             "timeScore": 90, "comment": "流程验证"})
    checked = q("SELECT COUNT(*) AS c FROM interview_participant WHERE session_id=%s "
                "AND checkin_status='CHECKED_IN'", (sid,), one=True)["c"]
    scored = q("SELECT COUNT(DISTINCT intent_id) AS c FROM interview_score WHERE session_id=%s",
               (sid,), one=True)["c"]
    check(checked == len(participants), "全部签到", f"{checked}/{len(participants)}")
    check(scored == len(participants), "全部完成评分", f"{scored}/{len(participants)}")

    print("\n== 阶段 5 拟录取与学生确认 ==")
    api("POST", f"/api/stages/{st['CONFIRM']['stage_id']}/open", t)
    check(stage("CONFIRM")["status"] == "OPEN", "开启拟录取与学生确认阶段")
    check(rnd(1)["status"] == "FILLING", "首轮选报维持填报中")
    s1_intent = q("SELECT intent_id FROM intent WHERE student_id=(SELECT student_id FROM student "
                  "WHERE student_no='S2026001') AND status='SUBMITTED' ORDER BY parallel_no LIMIT 1",
                  one=True)["intent_id"]
    s2_intent = q("SELECT intent_id FROM intent WHERE student_id=(SELECT student_id FROM student "
                  "WHERE student_no='S2026002') AND status='SUBMITTED' ORDER BY parallel_no LIMIT 1",
                  one=True)["intent_id"]
    code, data = api("POST", "/api/results/propose", t, {"intentId": s1_intent})
    check(code == 200, "教师对 S2026001 发起拟录取", str(data))
    code, data = api("POST", "/api/results/propose", t, {"intentId": s2_intent})
    check(code == 200, "教师对 S2026002 发起拟录取", str(data))
    res1 = q("SELECT r.result_id FROM selection_result r JOIN admission a "
             "ON a.student_id = r.student_id AND a.lab_id = r.lab_id AND a.round_id = r.round_id "
             "WHERE a.status = 'OCCUPIED' AND r.student_id = "
             "(SELECT student_id FROM student WHERE student_no='S2026001') "
             "ORDER BY r.result_id DESC LIMIT 1", one=True)
    code, data = api("POST", f"/api/results/{res1['result_id']}/confirm", s1)
    check(code == 200, "S2026001 手动确认录取", str(data))
    check(q("SELECT status FROM admission WHERE student_id=(SELECT student_id FROM student "
            "WHERE student_no='S2026001') ORDER BY admission_id DESC LIMIT 1", one=True)["status"]
          == "CONFIRMED", "确认后录取状态为 CONFIRMED")
    code, data = api("POST", f"/api/stages/{st['CONFIRM']['stage_id']}/close", t)
    check(code == 200, "关闭拟录取与学生确认阶段", str(data))
    check(rnd(1)["status"] == "CLOSED", "首轮选报关联合动生效")
    s2_adm = q("SELECT status, confirm_type FROM admission WHERE student_id=(SELECT student_id FROM "
               "student WHERE student_no='S2026002') ORDER BY admission_id DESC LIMIT 1", one=True)
    check(s2_adm["status"] == "CONFIRMED" and s2_adm["confirm_type"] == "AUTO",
          "仅一份拟录取未确认的学生自动确认", str(s2_adm))

    print("\n== 阶段 6 分轮补录 ==")
    code, data = api("POST", f"/api/stages/{st['SUPPLEMENT']['stage_id']}/open", t)
    check(code == 200 and stage("SUPPLEMENT")["status"] == "OPEN", "开启分轮补录阶段")
    check(rnd(2)["status"] == "FILLING", "分轮补录开启时固定开放第二轮补录", str(rnd(2)))
    check(rnd(3)["status"] != "FILLING", "第三轮补录保持关闭")
    code, app3 = api("POST", "/api/filing/applications", s3)
    check(code == 200, "未录取学生进入补录建单", str(app3))
    code, data = api("PUT", f"/api/filing/applications/{app3['applicationId']}/intents", s3,
                     {"mentorIds": mentor_ids[:2]})
    check(code == 400 or code == 409 or code == 422, "补录意向超过 1 个被拒绝", str(data))
    api("PUT", f"/api/filing/applications/{app3['applicationId']}/intents", s3,
        {"mentorIds": mentor_ids[:1]})
    code, data = api("POST", f"/api/filing/applications/{app3['applicationId']}/submit", s3)
    check(code == 200, "提交第二轮补录意向", str(data))
    code, _ = api("POST", f"/api/rounds/{rnd(2)['round_id']}/close", t)
    check(code == 200, "关闭第二轮补录", str(code))
    code, data = api("POST", f"/api/rounds/{rnd(3)['round_id']}/open", t)
    check(code == 200 and rnd(3)["status"] == "FILLING", "开放第三轮补录", str(data))
    check(stage("SUPPLEMENT")["status"] == "OPEN", "第三轮补录开启时阶段处于分轮补录")
    code, data = api("POST", f"/api/rounds/{rnd(3)['round_id']}/close", t)
    check(code == 200, "关闭第三轮补录", str(data))
    check(data.get("closedSupplementStage") is True and stage("SUPPLEMENT")["status"] == "CLOSED",
          "第三轮补录关闭时分轮补录阶段同步关闭", str(data))

    print("\n== 阶段 7 统筹调剂录取 ==")
    code, data = api("POST", f"/api/stages/{st['ADJUST']['stage_id']}/open", t)
    check(code == 200 and stage("ADJUST")["status"] == "OPEN", "开启统筹调剂录取阶段")
    check(rnd(4)["status"] == "FILLING", "统筹调剂轮次同步开放填报", str(rnd(4)))
    code, data = api("POST", "/api/adjustments/match", t, {"roundId": rnd(4)["round_id"]})
    check(code == 200, "运行自动调剂匹配", str(data))
    unplaced_sql = """
        SELECT s.student_id, s.student_no FROM student s
        WHERE NOT EXISTS (SELECT 1 FROM admission a WHERE a.student_id = s.student_id
                          AND a.status IN ('OCCUPIED','CONFIRMED','EFFECTIVE','ENROLLED'))"""
    for _ in range(3):
        unplaced = q(unplaced_sql)
        if not unplaced:
            break
        for stu in unplaced:
            adj = q("SELECT adjust_id FROM adjustment WHERE student_id=%s AND status IN "
                    "('PENDING','REJECTED') ORDER BY adjust_id DESC LIMIT 1",
                    (stu["student_id"],), one=True)
            if not adj:
                continue
            cand = q("SELECT l.lab_id FROM lab l WHERE l.status='OPEN' ORDER BY "
                     "(l.quota - (SELECT COUNT(*) FROM admission a WHERE a.lab_id=l.lab_id "
                     "AND a.status IN ('OCCUPIED','CONFIRMED','EFFECTIVE','ENROLLED'))) DESC, "
                     "l.lab_id LIMIT 1", one=True)
            code, nres = api("POST", f"/api/adjustments/{adj['adjust_id']}/negotiate", t,
                             {"conclusion": "ACCEPT", "method": "PHONE",
                              "finalLabId": cand["lab_id"], "remark": "流程验证约谈"})
            print(f"    约谈 {stu['student_no']} → 实训室{cand['lab_id']}: {code}")
    check(not q(unplaced_sql), "约谈后无遗留待安置学生",
          str([r["student_no"] for r in q(unplaced_sql)]))
    code, data = api("POST", f"/api/rounds/{rnd(4)['round_id']}/close", t)
    check(code == 200, "关闭统筹调剂轮次", str(data))
    api("POST", f"/api/stages/{st['ADJUST']['stage_id']}/close", t)
    check(stage("ADJUST")["status"] == "CLOSED", "关闭统筹调剂录取阶段")

    print("\n== 阶段 8 结果公示与异议 ==")
    api("POST", f"/api/stages/{st['PUBLICITY']['stage_id']}/open", t)
    check(stage("PUBLICITY")["status"] == "OPEN", "开启公示阶段")
    now = datetime.now()
    code, batch = api("POST", "/api/publicity/batches", t,
                      {"roundId": None, "startTime": (now - timedelta(minutes=1)).strftime("%Y-%m-%dT%H:%M:%S"),
                       "endTime": (now + timedelta(days=1)).strftime("%Y-%m-%dT%H:%M:%S"),
                       "remark": "流程验证公示"})
    check(code == 200, "发布全部轮次公示", str(batch))
    bid = batch.get("batchId") or batch.get("batch_id")
    admitted = q("SELECT COUNT(*) AS c FROM admission WHERE status IN "
                 "('CONFIRMED','EFFECTIVE','ENROLLED')", one=True)["c"]
    items = q("SELECT COUNT(*) AS c FROM publicity_item WHERE batch_id=%s", (bid,), one=True)["c"]
    check(items == admitted, "公示收录全部已录取学生", f"items={items}, admitted={admitted}")
    code, data = api("POST", "/api/objections", s1, {"batchId": bid, "content": "有疑问"})
    check(code == 400, "少于 5 字的异议被拒绝", str(data))
    code, obj = api("POST", "/api/objections", s1, {"batchId": bid, "content": "对该录取结果存有疑问"})
    check(code == 200, "提交有效异议", str(obj))
    code, data = api("POST", f"/api/objections/{obj['objectionId']}/handle", t,
                     {"status": "RESOLVED", "resolution": "已核实并答复学生"})
    check(code == 200, "教师处理异议", str(data))
    check(q("SELECT status FROM objection WHERE objection_id=%s", (obj["objectionId"],),
            one=True)["status"] == "RESOLVED", "异议状态更新为已解决")
    api("POST", f"/api/stages/{st['PUBLICITY']['stage_id']}/close", t)
    check(stage("PUBLICITY")["status"] == "CLOSED", "关闭公示阶段")

    print("\n== 结果汇总 ==")
    print(f"通过 {len(PASSED)} 项，失败 {len(FAILED)} 项")
    if FAILED:
        for item in FAILED:
            print("  失败：", item)
        return 1
    return 0


if __name__ == "__main__":
    try:
        code = main()
    finally:
        print("\n正在恢复演示数据…")
        try:
            restore_demo()
            print("演示数据已恢复")
        except Exception as exc:  # noqa: BLE001
            print("恢复演示数据失败：", exc)
    sys.exit(code)
