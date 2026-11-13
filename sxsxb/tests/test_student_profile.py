"""评分时查看学生注册简历（场次学生简历）单元测试。

覆盖 filing_api.get_session_student_profile：
1. 实训室负责人可查看本室场次学生的完整简历（含年级排名/获奖情况/擅长方向）；
2. 该场次学生所选导师可查看（即使不是负责人/评委）；
3. 场次评委可查看；
4. 与该场次无关的教师返回 403，且不返回学生数据；
5. 学生不在本场次名单中返回 404；
6. 面试场次不存在返回 404；
7. 学生账号无法通过教师身份校验，返回 403。

测试直接调用接口函数并读写 MySQL，无需启动 Web 服务。
所有测试数据（临时账号/教师/学生/实训室/意向/场次）在 tearDown 中清理，不影响现有业务数据。

运行：
    $env:DB_PASSWORD='050613ying'
    python tests/test_student_profile.py -v
"""
import os
import sys
import time
import unittest
from datetime import datetime, timedelta

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, ROOT)
os.environ.setdefault("DB_PASSWORD", "050613ying")

import pymysql  # noqa: E402
from fastapi import HTTPException  # noqa: E402
from pymysql.cursors import DictCursor  # noqa: E402

import filing_api  # noqa: E402


def connect():
    return pymysql.connect(**{**filing_api.DB_CONFIG, "cursorclass": DictCursor})


class SessionStudentProfileTest(unittest.TestCase):
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
            cur.execute("SELECT MIN(round_id) AS round_id FROM selection_round")
            cls.round_id = cur.fetchone()["round_id"]

    def setUp(self):
        self.suffix = str(time.time_ns())[-9:]
        self.account_ids = []
        self.teacher_ids = []
        self.student_ids = []
        with self.conn.cursor() as cur:
            if not self.round_id:
                self.skipTest("数据库缺少选报轮次")

            # 临时实训室
            cur.execute(
                """
                INSERT INTO lab (lab_code, name, college, direction, intro, quota, status)
                VALUES (%s, %s, '单测学院', '方向', '简介', 5, 'OPEN')
                """,
                (f"UTPL{self.suffix}", f"单测实训室{self.suffix}"),
            )
            self.lab_id = cur.lastrowid

            # 负责人（兼导师）、场次学生所选导师、无关教师
            self.owner = self.add_teacher(cur, "负责人")
            self.mentor = self.add_teacher(cur, "学生导师")
            self.outsider = self.add_teacher(cur, "无关教师")
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
                    self.mentor["teacher_id"], self.lab_id, granted_by,
                ),
            )
            cur.execute(
                """
                INSERT INTO mentor (teacher_id, lab_id, research_direction, capacity, status)
                VALUES (%s, %s, '大模型应用', 3, 'ACTIVE'),
                       (%s, %s, '数据挖掘', 3, 'ACTIVE')
                """,
                (
                    self.owner["teacher_id"], self.lab_id,
                    self.mentor["teacher_id"], self.lab_id,
                ),
            )
            cur.execute(
                "SELECT mentor_id, teacher_id FROM mentor WHERE lab_id = %s",
                (self.lab_id,),
            )
            self.mentors = {m["teacher_id"]: m["mentor_id"] for m in cur.fetchall()}

            # 学生：场次内学生、名单外学生
            self.in_session = self.add_student(
                cur, "场内学生", grade_rank="6/128",
                awards="全国大学生数学建模竞赛省一等奖", specialty="Python、机器学习",
            )
            self.not_in_session = self.add_student(
                cur, "场外学生", grade_rank="30/128", awards=None, specialty=None,
            )

            # 意向申请与意向
            cur.execute(
                """
                INSERT INTO intent_application
                  (round_id, student_id, accept_internal_adjust, accept_cross_adjust,
                   intent_count, status, submitted_at)
                VALUES (%s, %s, 1, 1, 1, 'SUBMITTED', NOW())
                """,
                (self.round_id, self.in_session["student_id"]),
            )
            application_id = cur.lastrowid
            cur.execute(
                """
                INSERT INTO intent
                  (application_id, round_id, student_id, mentor_id, lab_id,
                   parallel_no, status, submitted_at)
                VALUES (%s, %s, %s, %s, %s, 1, 'SUBMITTED', NOW())
                """,
                (
                    application_id, self.round_id, self.in_session["student_id"],
                    self.mentors[self.mentor["teacher_id"]], self.lab_id,
                ),
            )
            self.intent_id = cur.lastrowid

            # 面试场次与场次学生
            cur.execute(
                """
                INSERT INTO interview_session
                  (round_id, lab_id, session_name, start_time, end_time, location,
                   organizer_teacher_id, status)
                VALUES (%s, %s, '单测场次', %s, %s, '实训楼 101', %s, 'PLANNED')
                """,
                (
                    self.round_id, self.lab_id,
                    datetime.now(), datetime.now() + timedelta(hours=1),
                    self.owner["teacher_id"],
                ),
            )
            self.session_id = cur.lastrowid
            cur.execute(
                """
                INSERT INTO interview_participant (session_id, intent_id, student_id)
                VALUES (%s, %s, %s)
                """,
                (self.session_id, self.intent_id, self.in_session["student_id"]),
            )
        self.conn.commit()

    def tearDown(self):
        with self.conn.cursor() as cur:
            cur.execute("DELETE FROM interview_participant WHERE session_id = %s", (self.session_id,))
            cur.execute("DELETE FROM interview_judge WHERE session_id = %s", (self.session_id,))
            cur.execute("DELETE FROM interview_session WHERE session_id = %s", (self.session_id,))
            cur.execute("DELETE FROM intent WHERE intent_id = %s", (self.intent_id,))
            cur.execute(
                "DELETE FROM intent_application WHERE student_id IN ({})".format(
                    ",".join(["%s"] * len(self.student_ids))
                ),
                tuple(self.student_ids),
            )
            cur.execute(
                "DELETE FROM student WHERE student_id IN ({})".format(
                    ",".join(["%s"] * len(self.student_ids))
                ),
                tuple(self.student_ids),
            )
            cur.execute("DELETE FROM mentor WHERE lab_id = %s", (self.lab_id,))
            cur.execute("DELETE FROM teacher_role WHERE scope_lab_id = %s", (self.lab_id,))
            cur.execute(
                "DELETE FROM teacher WHERE teacher_id IN ({})".format(
                    ",".join(["%s"] * len(self.teacher_ids))
                ),
                tuple(self.teacher_ids),
            )
            cur.execute("DELETE FROM lab WHERE lab_id = %s", (self.lab_id,))
            cur.execute(
                "DELETE FROM account WHERE account_id IN ({})".format(
                    ",".join(["%s"] * len(self.account_ids))
                ),
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
            (f"UTP{self.suffix}{len(self.account_ids)}",
             f"utp_t_{self.suffix}_{len(self.account_ids)}"),
        )
        account_id = cur.lastrowid
        self.account_ids.append(account_id)
        cur.execute(
            """
            INSERT INTO teacher (account_id, teacher_no, name, college, title, status)
            VALUES (%s, %s, %s, '单测学院', '讲师', 'ACTIVE')
            """,
            (account_id, f"UTP{self.suffix}{len(self.teacher_ids)}", f"单测{label}"),
        )
        teacher_id = cur.lastrowid
        self.teacher_ids.append(teacher_id)
        return {"account_id": account_id, "teacher_id": teacher_id, "user_type": "TEACHER"}

    def add_student(self, cur, label, grade_rank, awards, specialty):
        cur.execute(
            """
            INSERT INTO account (user_no, user_type, login_name, password_hash, status)
            VALUES (%s, 'STUDENT', %s, 'x', 'ACTIVE')
            """,
            (f"UTPS{self.suffix}{len(self.account_ids)}",
             f"utp_s_{self.suffix}_{len(self.account_ids)}"),
        )
        account_id = cur.lastrowid
        self.account_ids.append(account_id)
        cur.execute(
            """
            INSERT INTO student
              (account_id, student_no, name, gender, grade, major, college, phone,
               grade_rank, awards, specialty, selection_state)
            VALUES (%s, %s, %s, '女', '2024', '软件工程', '单测学院', '13800000000',
                    %s, %s, %s, 'FILLED')
            """,
            (
                account_id, f"UTPS{self.suffix}{len(self.student_ids)}", f"单测{label}",
                grade_rank, awards, specialty,
            ),
        )
        student_id = cur.lastrowid
        self.student_ids.append(student_id)
        return {"account_id": account_id, "student_id": student_id, "user_type": "STUDENT"}

    def add_judge(self, teacher_id):
        with self.conn.cursor() as cur:
            cur.execute(
                """
                INSERT INTO interview_judge (session_id, teacher_id, judge_role)
                VALUES (%s, %s, 'JUDGE')
                """,
                (self.session_id, teacher_id),
            )
        self.conn.commit()

    # ---------------- 读取简历 ----------------

    def test_lab_owner_reads_full_profile(self):
        """负责人可读取本室场次学生的完整简历。"""
        result = filing_api.get_session_student_profile(
            self.session_id, self.in_session["student_id"], self.owner
        )

        self.assertEqual(result["student_id"], self.in_session["student_id"])
        self.assertEqual(result["student_no"], f"UTPS{self.suffix}0")
        self.assertEqual(result["name"], "单测场内学生")
        self.assertEqual(result["gender"], "女")
        self.assertEqual(result["grade"], "2024")
        self.assertEqual(result["major"], "软件工程")
        self.assertEqual(result["phone"], "13800000000")
        self.assertEqual(result["grade_rank"], "6/128")
        self.assertEqual(result["awards"], "全国大学生数学建模竞赛省一等奖")
        self.assertEqual(result["specialty"], "Python、机器学习")
        self.assertEqual(result["selection_state"], "FILLED")

    def test_participant_mentor_can_read(self):
        """场次学生所选导师可查看其简历（非负责人、非评委）。"""
        result = filing_api.get_session_student_profile(
            self.session_id, self.in_session["student_id"], self.mentor
        )
        self.assertEqual(result["student_id"], self.in_session["student_id"])

    def test_judge_can_read(self):
        """场次评委可查看场次学生简历。"""
        self.add_judge(self.outsider["teacher_id"])

        result = filing_api.get_session_student_profile(
            self.session_id, self.in_session["student_id"], self.outsider
        )
        self.assertEqual(result["grade_rank"], "6/128")

    def test_unrelated_teacher_forbidden(self):
        """与本场次无关的教师返回 403。"""
        with self.assertRaises(HTTPException) as ctx:
            filing_api.get_session_student_profile(
                self.session_id, self.in_session["student_id"], self.outsider
            )
        self.assertEqual(ctx.exception.status_code, 403)

    def test_student_not_in_session_returns_404(self):
        """学生不在本场次名单中返回 404。"""
        with self.assertRaises(HTTPException) as ctx:
            filing_api.get_session_student_profile(
                self.session_id, self.not_in_session["student_id"], self.owner
            )
        self.assertEqual(ctx.exception.status_code, 404)

    def test_missing_session_returns_404(self):
        """面试场次不存在返回 404。"""
        with self.conn.cursor() as cur:
            cur.execute("SELECT MAX(session_id) + 1000 AS session_id FROM interview_session")
            missing_id = cur.fetchone()["session_id"]

        with self.assertRaises(HTTPException) as ctx:
            filing_api.get_session_student_profile(
                missing_id, self.in_session["student_id"], self.owner
            )
        self.assertEqual(ctx.exception.status_code, 404)

    def test_student_account_blocked(self):
        """学生账号无教师身份，返回 403。"""
        student_user = {
            "account_id": self.in_session["account_id"],
            "teacher_id": None,
            "user_type": "STUDENT",
        }

        with self.assertRaises(HTTPException) as ctx:
            filing_api.current_teacher(student_user)
        self.assertEqual(ctx.exception.status_code, 403)

        with self.assertRaises(HTTPException) as ctx:
            filing_api.get_session_student_profile(
                self.session_id, self.in_session["student_id"], student_user
            )
        self.assertEqual(ctx.exception.status_code, 403)


if __name__ == "__main__":
    unittest.main(verbosity=2)
