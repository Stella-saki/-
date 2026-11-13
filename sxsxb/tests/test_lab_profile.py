"""实训室信息（公开/编辑权限）单元测试。

覆盖 filing_api.lab_profiles、update_lab_profile、update_my_intro：
1. 学生/教师读取实训室信息：返回主要方向、简介、导师团队（含导师简介），只含在职导师；
2. 实训室负责人可修改本室主要方向与简介，并写入 LAB_PROFILE_UPDATE 审计；
3. 非本室负责人修改返回 403；统筹岗可修改任意实训室；
4. 教师可修改本人简介，并写入 TEACHER_INTRO_UPDATE 审计；
5. 简介超长返回 400，超长方向/简介按列宽截断，空简介清空为 NULL；
6. 学生账号无法调用教师侧修改接口（current_teacher 拦截）。

测试直接调用接口函数并读写 MySQL，无需启动 Web 服务。
所有测试数据（临时账号/教师/实训室/导师/审计）在 tearDown 中清理，不影响现有业务数据。

运行：
    $env:DB_PASSWORD='050613ying'
    python tests/test_lab_profile.py -v
"""
import os
import sys
import time
import unittest
from datetime import datetime

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, ROOT)
os.environ.setdefault("DB_PASSWORD", "050613ying")

import pymysql  # noqa: E402
from fastapi import HTTPException  # noqa: E402
from pymysql.cursors import DictCursor  # noqa: E402

import filing_api  # noqa: E402


def connect():
    return pymysql.connect(**{**filing_api.DB_CONFIG, "cursorclass": DictCursor})


class LabProfileTest(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.conn = connect()
        with cls.conn.cursor() as cur:
            cur.execute(
                """
                SELECT tr.teacher_id, t.account_id
                FROM teacher_role tr
                JOIN teacher t ON t.teacher_id = tr.teacher_id
                WHERE tr.role_code = 'COORDINATOR' AND tr.status = 'ACTIVE'
                LIMIT 1
                """
            )
            cls.coordinator = cur.fetchone()

    def setUp(self):
        self.start_time = datetime.now()
        self.suffix = str(time.time_ns())[-9:]
        self.account_ids = []
        self.teacher_ids = []
        with self.conn.cursor() as cur:
            # 临时实训室
            cur.execute(
                """
                INSERT INTO lab (lab_code, name, college, direction, intro, quota, status)
                VALUES (%s, %s, '单测学院', '原方向', '原简介', 5, 'OPEN')
                """,
                (f"UTL{self.suffix}", f"单测实训室{self.suffix}"),
            )
            self.lab_id = cur.lastrowid

            # 三个教师：负责人、普通导师、用于验证“非在职导师不展示”的导师
            self.owner, self.other, self.inactive = (
                self.add_teacher(cur, "负责人"),
                self.add_teacher(cur, "普通导师"),
                self.add_teacher(cur, "离职导师"),
            )
            granted_by = (self.coordinator or {}).get("account_id") or self.owner["account_id"]
            cur.execute(
                """
                INSERT INTO teacher_role
                  (teacher_id, role_code, scope_lab_id, status, granted_by)
                VALUES (%s, 'LAB_OWNER', %s, 'ACTIVE', %s),
                       (%s, 'MENTOR', %s, 'ACTIVE', %s),
                       (%s, 'MENTOR', %s, 'ACTIVE', %s)
                """,
                (
                    self.owner["teacher_id"], self.lab_id, granted_by,
                    self.owner["teacher_id"], self.lab_id, granted_by,
                    self.other["teacher_id"], self.lab_id, granted_by,
                ),
            )
            cur.execute(
                """
                INSERT INTO mentor
                  (teacher_id, lab_id, research_direction, capacity, status)
                VALUES (%s, %s, '大模型应用', 3, 'ACTIVE'),
                       (%s, %s, '数据挖掘', 2, 'ACTIVE'),
                       (%s, %s, '已停招方向', 1, 'INACTIVE')
                """,
                (
                    self.owner["teacher_id"], self.lab_id,
                    self.other["teacher_id"], self.lab_id,
                    self.inactive["teacher_id"], self.lab_id,
                ),
            )
            # 导师简介
            cur.execute(
                "UPDATE teacher SET intro = '本人简介初始内容' WHERE teacher_id IN (%s, %s)",
                (self.owner["teacher_id"], self.other["teacher_id"]),
            )
        self.conn.commit()

    def tearDown(self):
        with self.conn.cursor() as cur:
            cur.execute(
                """
                DELETE FROM audit_log
                WHERE created_at >= %s
                  AND action_code IN ('LAB_PROFILE_UPDATE', 'TEACHER_INTRO_UPDATE')
                  AND biz_id IN ({})
                """.format(",".join(["%s"] * (1 + len(self.teacher_ids)))),
                (self.start_time, self.lab_id, *self.teacher_ids),
            )
            cur.execute("DELETE FROM mentor WHERE lab_id = %s", (self.lab_id,))
            cur.execute("DELETE FROM teacher_role WHERE scope_lab_id = %s", (self.lab_id,))
            placeholders = ",".join(["%s"] * len(self.teacher_ids))
            cur.execute(
                f"DELETE FROM teacher WHERE teacher_id IN ({placeholders})",
                tuple(self.teacher_ids),
            )
            cur.execute("DELETE FROM lab WHERE lab_id = %s", (self.lab_id,))
            placeholders = ",".join(["%s"] * len(self.account_ids))
            cur.execute(
                f"DELETE FROM account WHERE account_id IN ({placeholders})",
                tuple(self.account_ids),
            )
        self.conn.commit()

    # ---------------- 辅助 ----------------

    def add_teacher(self, cur, label):
        cur.execute(
            """
            INSERT INTO account (user_no, user_type, login_name, password_hash, status)
            VALUES (%s, 'TEACHER', %s, 'x', 'ACTIVE')
            """,
            (f"UTT{self.suffix}{len(self.account_ids)}", f"ut_t_{self.suffix}_{len(self.account_ids)}"),
        )
        account_id = cur.lastrowid
        self.account_ids.append(account_id)
        cur.execute(
            """
            INSERT INTO teacher (account_id, teacher_no, name, college, title, status)
            VALUES (%s, %s, %s, '单测学院', '讲师', 'ACTIVE')
            """,
            (account_id, f"UTT{self.suffix}{len(self.teacher_ids)}", f"单测{label}"),
        )
        teacher_id = cur.lastrowid
        self.teacher_ids.append(teacher_id)
        return {
            "account_id": account_id,
            "teacher_id": teacher_id,
            "user_type": "TEACHER",
        }

    def lab_row(self):
        with self.conn.cursor() as cur:
            cur.execute(
                "SELECT direction, intro FROM lab WHERE lab_id = %s", (self.lab_id,)
            )
            return cur.fetchone()

    def teacher_row(self, teacher_id):
        with self.conn.cursor() as cur:
            cur.execute(
                "SELECT intro FROM teacher WHERE teacher_id = %s", (teacher_id,)
            )
            return cur.fetchone()

    def audits(self, action_code, biz_id):
        with self.conn.cursor() as cur:
            cur.execute(
                """
                SELECT before_value, after_value FROM audit_log
                WHERE created_at >= %s AND action_code = %s AND biz_id = %s
                ORDER BY log_id DESC
                """,
                (self.start_time, action_code, biz_id),
            )
            return cur.fetchall()

    # ---------------- 读取 ----------------

    def test_profiles_include_direction_intro_and_active_mentors(self):
        """实训室信息包含主要方向、简介与在职导师团队（含职称/研究方向/简介）。"""
        result = filing_api.lab_profiles({"user_type": "STUDENT", "teacher_id": None})

        lab = next((x for x in result["items"] if x["lab_id"] == self.lab_id), None)
        self.assertIsNotNone(lab)
        self.assertEqual(lab["direction"], "原方向")
        self.assertEqual(lab["intro"], "原简介")
        self.assertEqual(lab["quota"], 5)

        self.assertEqual(len(lab["mentors"]), 2, "非在职导师不应出现在导师团队中")
        by_teacher = {m["teacher_id"]: m for m in lab["mentors"]}
        self.assertIn(self.owner["teacher_id"], by_teacher)
        self.assertIn(self.other["teacher_id"], by_teacher)
        self.assertNotIn(self.inactive["teacher_id"], by_teacher)
        owner = by_teacher[self.owner["teacher_id"]]
        self.assertEqual(owner["title"], "讲师")
        self.assertEqual(owner["research_direction"], "大模型应用")
        self.assertEqual(owner["intro"], "本人简介初始内容")
        self.assertEqual(owner["capacity"], 3)

    # ---------------- 实训室信息编辑 ----------------

    def test_lab_owner_updates_profile_and_writes_audit(self):
        """负责人可修改本室主要方向与简介，并记录审计。"""
        result = filing_api.update_lab_profile(
            self.lab_id,
            {"direction": "人工智能与大模型", "intro": "更新后的实训室简介"},
            self.owner,
        )

        self.assertEqual(result["labId"], self.lab_id)
        self.assertEqual(result["direction"], "人工智能与大模型")
        self.assertEqual(result["intro"], "更新后的实训室简介")
        row = self.lab_row()
        self.assertEqual(row["direction"], "人工智能与大模型")
        self.assertEqual(row["intro"], "更新后的实训室简介")

        audits = self.audits("LAB_PROFILE_UPDATE", self.lab_id)
        self.assertEqual(len(audits), 1)
        self.assertIn("原方向", audits[0]["before_value"])
        self.assertIn("人工智能与大模型", audits[0]["after_value"])

    def test_non_owner_cannot_update_lab_profile(self):
        """非本室负责人（同室普通导师）修改本室信息返回 403。"""
        with self.assertRaises(HTTPException) as ctx:
            filing_api.update_lab_profile(
                self.lab_id, {"direction": "越权方向", "intro": "越权简介"}, self.other
            )

        self.assertEqual(ctx.exception.status_code, 403)
        self.assertEqual(self.lab_row()["direction"], "原方向")
        self.assertFalse(self.audits("LAB_PROFILE_UPDATE", self.lab_id))

    def test_coordinator_can_update_any_lab_profile(self):
        """统筹岗可维护任意实训室信息。"""
        if not self.coordinator:
            self.skipTest("数据库缺少统筹岗账号")
        user = {
            "account_id": self.coordinator["account_id"],
            "teacher_id": self.coordinator["teacher_id"],
            "user_type": "TEACHER",
        }

        result = filing_api.update_lab_profile(
            self.lab_id, {"direction": "统筹岗调整方向", "intro": "统筹岗调整简介"}, user
        )

        self.assertEqual(result["direction"], "统筹岗调整方向")
        self.assertEqual(self.lab_row()["intro"], "统筹岗调整简介")

    def test_update_missing_lab_returns_404(self):
        """实训室不存在时返回 404。"""
        if not self.coordinator:
            self.skipTest("数据库缺少统筹岗账号")
        user = {
            "account_id": self.coordinator["account_id"],
            "teacher_id": self.coordinator["teacher_id"],
            "user_type": "TEACHER",
        }
        with self.conn.cursor() as cur:
            cur.execute("SELECT MAX(lab_id) + 1000 AS lab_id FROM lab")
            missing_id = cur.fetchone()["lab_id"]

        with self.assertRaises(HTTPException) as ctx:
            filing_api.update_lab_profile(missing_id, {"direction": "x", "intro": "y"}, user)
        self.assertEqual(ctx.exception.status_code, 404)

    def test_profile_fields_truncated_to_column_width(self):
        """超出列宽的方向（255）与简介（1000）按列宽截断，避免写入报错。"""
        result = filing_api.update_lab_profile(
            self.lab_id,
            {"direction": "方" * 300, "intro": "介" * 1200},
            self.owner,
        )

        self.assertEqual(len(result["direction"]), 255)
        self.assertEqual(len(result["intro"]), 1000)
        row = self.lab_row()
        self.assertEqual(len(row["direction"]), 255)
        self.assertEqual(len(row["intro"]), 1000)

    # ---------------- 教师个人简介 ----------------

    def test_teacher_updates_own_intro_and_writes_audit(self):
        """教师可修改本人简介，并记录审计。"""
        result = filing_api.update_my_intro({"intro": "新的个人简介"}, self.owner)

        self.assertEqual(result["teacherId"], self.owner["teacher_id"])
        self.assertEqual(result["intro"], "新的个人简介")
        self.assertEqual(self.teacher_row(self.owner["teacher_id"])["intro"], "新的个人简介")
        audits = self.audits("TEACHER_INTRO_UPDATE", self.owner["teacher_id"])
        self.assertEqual(len(audits), 1)
        self.assertIn("本人简介初始内容", audits[0]["before_value"])
        self.assertIn("新的个人简介", audits[0]["after_value"])
        # 不影响其他教师
        self.assertEqual(
            self.teacher_row(self.other["teacher_id"])["intro"], "本人简介初始内容"
        )

    def test_intro_too_long_returns_400(self):
        """教师简介超过 500 字返回 400。"""
        with self.assertRaises(HTTPException) as ctx:
            filing_api.update_my_intro({"intro": "字" * 501}, self.owner)

        self.assertEqual(ctx.exception.status_code, 400)
        self.assertEqual(
            self.teacher_row(self.owner["teacher_id"])["intro"], "本人简介初始内容"
        )

    def test_blank_intro_clears_value(self):
        """空白简介视为清空，写入 NULL。"""
        filing_api.update_my_intro({"intro": "   "}, self.owner)

        self.assertIsNone(self.teacher_row(self.owner["teacher_id"])["intro"])

    # ---------------- 权限入口 ----------------

    def test_student_blocked_by_teacher_dependency(self):
        """学生账号无法通过教师身份校验，修改类接口被 403 拦截。"""
        student = {"account_id": 0, "teacher_id": None, "user_type": "STUDENT"}

        with self.assertRaises(HTTPException) as ctx:
            filing_api.current_teacher(student)
        self.assertEqual(ctx.exception.status_code, 403)

        with self.assertRaises(HTTPException) as ctx:
            filing_api.update_lab_profile(self.lab_id, {"direction": "x"}, student)
        self.assertEqual(ctx.exception.status_code, 403)
        self.assertEqual(self.lab_row()["direction"], "原方向")


if __name__ == "__main__":
    unittest.main(verbosity=2)
