"""一键将 sxsxb 数据库恢复为初始状态（删除重建 + 建表 + 初始化演示数据）。

用法：
    python reset_db.py

连接参数读取环境变量：DB_HOST / DB_PORT / DB_USER / DB_PASSWORD / DB_NAME。
注意：会清空库内全部业务数据，请确认无需保留后再执行。
"""
import os
import sys
import time

import pymysql
from pymysql.constants import CLIENT

HERE = os.path.dirname(os.path.abspath(__file__))
DB_NAME = os.getenv("DB_NAME", "sxsxb")
CONN = {
    "host": os.getenv("DB_HOST", "127.0.0.1"),
    "port": int(os.getenv("DB_PORT", "3306")),
    "user": os.getenv("DB_USER", "root"),
    "password": os.getenv("DB_PASSWORD", "123456"),
}
EXPECTED = {
    "score_scheme": 1,
    "selection_stage": 8,
    "selection_round": 4,
    "lab": 4,
    "account": 22,
    "teacher": 12,
    "mentor": 12,
    "teacher_role": 17,
    "student": 10,
}


def run_sql_file(conn, filename: str) -> None:
    with open(os.path.join(HERE, filename), encoding="utf-8") as fp:
        sql = fp.read()
    with conn.cursor() as cur:
        cur.execute(sql)
        while cur.nextset():
            pass


def main() -> int:
    started = time.time()

    admin = pymysql.connect(**CONN, autocommit=True, charset="utf8mb4")
    try:
        with admin.cursor() as cur:
            cur.execute(f"DROP DATABASE IF EXISTS `{DB_NAME}`")
            cur.execute(
                f"CREATE DATABASE `{DB_NAME}` DEFAULT CHARACTER SET utf8mb4 "
                f"COLLATE utf8mb4_0900_ai_ci"
            )
    finally:
        admin.close()

    conn = pymysql.connect(
        **CONN, database=DB_NAME, charset="utf8mb4",
        client_flag=CLIENT.MULTI_STATEMENTS, autocommit=True,
    )
    try:
        run_sql_file(conn, "schema.sql")
        run_sql_file(conn, "init_data.sql")

        with conn.cursor() as cur:
            print(f"数据库 {DB_NAME} 已重建，耗时 {time.time() - started:.2f}s")
            print("\n初始化数据核对：")
            ok = True
            for table, expected in EXPECTED.items():
                cur.execute(f"SELECT COUNT(*) FROM `{table}`")
                count = cur.fetchone()[0]
                flag = "OK" if count == expected else "!!"
                ok = ok and count == expected
                print(f"  [{flag}] {table:<16} {count} (期望 {expected})")

            cur.execute("SHOW TABLES")
            print(f"  表数量: {len(cur.fetchall())} (期望 27)")

            cur.execute(
                "SELECT stage_name, start_time, end_time, status "
                "FROM selection_stage ORDER BY stage_no"
            )
            print("\n阶段排期（以执行当天为 D 日）：")
            for name, start, end, status in cur.fetchall():
                print(f"  {name:<12} {start} ~ {end}  {status}")
    finally:
        conn.close()

    return 0 if ok else 1


if __name__ == "__main__":
    sys.exit(main())
