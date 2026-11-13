"""新增后端接口冒烟验证：通知、开放日、阶段/轮次、公示、异议、统计。"""
import json
import os
import sys
import urllib.error
import urllib.request

BASE = os.getenv("API_BASE", "http://127.0.0.1:8000")
ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, ROOT)

import pymysql  # noqa: E402

DB = dict(host="127.0.0.1", port=3306, user="root",
          password=os.getenv("DB_PASSWORD", "050613ying"), database="sxsxb",
          charset="utf8mb4", cursorclass=pymysql.cursors.DictCursor, autocommit=True)

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
        headers["Authorization"] = f"Bearer {token}"
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


def login(name, password="123456"):
    return api("POST", "/api/auth/login", {"loginName": name, "password": password})


def main():
    s1 = login("S2026001")
    check("学生登录", s1[0] == 200, s1)
    st = s1[1]["token"]
    t1 = login("T001")
    check("统筹教师登录", t1[0] == 200, t1)
    tk = t1[1]["token"]

    code, data = api("GET", "/api/notifications", token=st)
    check("通知列表", code == 200 and "items" in data and "unreadCount" in data, data)

    code, data = api("GET", "/api/stages", token=st)
    check("阶段列表", code == 200 and isinstance(data, (list, dict)), data)
    code, data2 = api("GET", "/api/rounds", token=st)
    check("轮次列表", code == 200 and isinstance(data2, (list, dict)), data2)

    code, data = api("GET", "/api/stats/overview", token=tk)
    check("统计概览", code == 200 and "summary" in data and "labs" in data,
          str(data)[:200])

    code, data = api("GET", "/api/open-days/reservations/my", token=st)
    check("我的开放日预约", code == 200, data)

    code, data = api("GET", "/api/publicity/batches", token=st)
    check("公示批次列表", code == 200, data)
    code, data = api("GET", "/api/publicity/my", token=st)
    check("我的公示", code == 200, data)

    code, data = api("GET", "/api/objections/my", token=st)
    check("我的异议", code == 200, data)

    code, data = api("GET", "/api/adjustments", token=tk)
    check("调剂列表", code == 200 and "items" in data, data)

    # 开放日预约（取第一个 lab 与明天）
    with pymysql.connect(**DB) as conn, conn.cursor() as cur:
        cur.execute("SELECT lab_id FROM lab ORDER BY lab_id LIMIT 1")
        lab_id = cur.fetchone()["lab_id"]
    code, data = api("POST", "/api/open-days/reservations",
                     {"labId": lab_id, "visitDate": "2026-12-20",
                      "timeSlot": "AM"}, token=st)
    check("开放日预约提交", code == 200, data)
    rid = data.get("reservationId") if isinstance(data, dict) else None
    if rid:
        code, data = api("POST", f"/api/open-days/reservations/{rid}/cancel", token=st)
        check("开放日预约取消", code == 200, data)

    print(f"\n结果：{PASS} 通过 / {FAIL} 失败")
    return 0 if FAIL == 0 else 1


if __name__ == "__main__":
    sys.exit(main())
