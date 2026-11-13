"""验证：意向填报不限导师人数，仅限制导师最终录取人数。"""
import json
import os
import sys
import urllib.error
import urllib.request

BASE = os.getenv("API_BASE", "http://127.0.0.1:8000")
os.environ.setdefault("DB_PASSWORD", "050613ying")
ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, ROOT)

import pymysql  # noqa: E402
from fastapi import HTTPException  # noqa: E402

import filing_api  # noqa: E402

DB = dict(host="127.0.0.1", port=3306, user="root",
          password=os.environ["DB_PASSWORD"], database="sxsxb",
          charset="utf8mb4", cursorclass=pymysql.cursors.DictCursor, autocommit=False)

PASS = 0
FAIL = 0


def check(name, cond, detail=""):
    global PASS, FAIL
    if cond:
        PASS += 1
        print(f"[PASS] {name}")
    else:
        FAIL += 1
        print(f"[FAIL] {name} {detail}")


def api(method, path, body=None, token=None):
    data = None
    headers = {"Accept": "application/json"}
    if body is not None:
        data = json.dumps(body, ensure_ascii=False).encode("utf-8")
        headers["Content-Type"] = "application/json; charset=utf-8"
    if token:
        headers["Authorization"] = "Bearer " + token
    req = urllib.request.Request(BASE + path, data=data, headers=headers, method=method)
    try:
        with urllib.request.urlopen(req, timeout=20) as resp:
            return resp.status, json.loads(resp.read().decode("utf-8"))
    except urllib.error.HTTPError as e:
        raw = e.read().decode("utf-8", "replace")
        try:
            return e.code, json.loads(raw)
        except Exception:
            return e.code, {"detail": raw}


def login(name):
    return api("POST", "/api/auth/login", {"loginName": name, "password": "123456"})


def main():
    students = ["S2026001", "S2026002", "S2026003", "S2026004", "S2026005"]
    target_mentor = 2  # capacity = 3
    created_apps = []

    conn = pymysql.connect(**DB)
    try:
        with conn.cursor() as cur:
            cur.execute("SELECT capacity FROM mentor WHERE mentor_id = %s", (target_mentor,))
            capacity = cur.fetchone()["capacity"]
            cur.execute(
                """DELETE i FROM intent i JOIN intent_application ia
                   ON ia.application_id = i.application_id
                   WHERE ia.student_id IN (
                     SELECT student_id FROM student WHERE student_no IN %s)""",
                (students,),
            )
            cur.execute(
                "DELETE FROM intent_application WHERE student_id IN "
                "(SELECT student_id FROM student WHERE student_no IN %s)",
                (students,),
            )
        conn.commit()

        # 1) 5 名学生同时选报同一导师（capacity=3），填报阶段应全部成功
        for no in students:
            code, data = login(no)
            check(f"登录 {no}", code == 200, data)
            if code != 200:
                continue
            token = data["token"]
            code, app = api("POST", "/api/filing/applications", token=token)
            if code != 200:
                check(f"{no} 创建申请", False, app)
                continue
            app_id = app["applicationId"]
            created_apps.append(app_id)
            code, saved = api("PUT", f"/api/filing/applications/{app_id}/intents",
                              {"mentorIds": [target_mentor],
                               "acceptInternalAdjust": False,
                               "acceptCrossAdjust": False}, token=token)
            check(f"{no} 保存意向（不限人数）", code == 200, saved)
            code, sub = api("POST", f"/api/filing/applications/{app_id}/submit", token=token)
            check(f"{no} 提交意向", code == 200, sub)

        with conn.cursor() as cur:
            cur.execute(
                """SELECT COUNT(*) AS cnt FROM intent
                   WHERE mentor_id = %s AND application_id IN (
                     SELECT application_id FROM intent_application WHERE student_id IN (
                       SELECT student_id FROM student WHERE student_no IN %s))""",
                (target_mentor, students),
            )
            cnt = cur.fetchone()["cnt"]
        check(f"同一导师选报人数不受容量限制（cap={capacity}，已选 {cnt} 人）",
              cnt == len(students), f"cnt={cnt}")

        # 2) 导师录取人数达到上限后，再录取应被拒绝
        with conn.cursor() as cur:
            cur.execute("SELECT student_id FROM student ORDER BY student_id LIMIT 5")
            sids = [r["student_id"] for r in cur.fetchall()]
        conn.begin()
        try:
            with conn.cursor() as cur:
                for sid in sids[:capacity]:
                    cur.execute(
                        """INSERT INTO admission
                           (student_id, lab_id, mentor_id, round_id, source_type,
                            confirm_type, status)
                           VALUES (%s, 1, %s, 1, 'ROUND1', 'MANUAL', 'OCCUPIED')""",
                        (sid, target_mentor),
                    )
                raised = None
                try:
                    filing_api.ensure_mentor_capacity(cur, target_mentor)
                except HTTPException as e:
                    raised = e
                check("导师录取达上限后拒绝继续录取",
                      raised is not None and raised.status_code == 409,
                      raised.detail if raised else "未抛出 409")
                print("       提示：" + (raised.detail if raised else ""))
        finally:
            conn.rollback()

        # 清理本次测试数据
        with conn.cursor() as cur:
            for app_id in created_apps:
                cur.execute("DELETE FROM intent WHERE application_id = %s", (app_id,))
                cur.execute("DELETE FROM intent_application WHERE application_id = %s", (app_id,))
            cur.execute(
                "UPDATE student SET selection_state = 'NOT_FILLED' WHERE student_no IN %s",
                (students,),
            )
        conn.commit()
    finally:
        conn.close()

    print(f"\n结果：{PASS} 通过 / {FAIL} 失败")
    return 0 if FAIL == 0 else 1


if __name__ == "__main__":
    sys.exit(main())
