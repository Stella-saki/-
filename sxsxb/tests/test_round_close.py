"""轮次关闭拟录取确认逻辑单元测试。

覆盖 filing_api.close_round_api 与 release_expired_admissions：
1. 仅 1 个拟录取：轮次关闭时自动确认；
2. 2~3 个拟录取：发送确认提醒并把确认截止设为 30 分钟后；
3. 30 分钟未确认：释放全部未确认名额并进入补录（SUPPLEMENT）；
4. 无拟录取的轮次关闭：不产生任何副作用；
5. 进入下一轮次：仅一份拟录取自动确认，多份未确认则释放并进入补录。

测试直接调用接口函数并读写 MySQL，需先保证数据库可连接（无需启动 Web 服务）。
所有测试数据（临时账号/学生/轮次/拟录取/通知/审计/名额流水）在 tearDown 中清理。

运行：
    $env:DB_PASSWORD='050613ying'
    python tests/test_round_close.py -v
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
from pymysql.cursors import DictCursor  # noqa: E402

import filing_api  # noqa: E402


def connect():
    return pymysql.connect(**{**filing_api.DB_CONFIG, "cursorclass": DictCursor})


class RoundCloseTest(unittest.TestCase):
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
            coordinator = cur.fetchone()
            if not coordinator:
                raise unittest.SkipTest("数据库缺少统筹岗账号，无法测试轮次关闭")
            cls.coordinator = {
                "account_id": coordinator["account_id"],
                "teacher_id": coordinator["teacher_id"],
            }
            cur.execute(
                """
                SELECT MIN(mentor_id) AS mentor_id, lab_id
                FROM mentor
                WHERE status = 'ACTIVE'
                GROUP BY lab_id
                ORDER BY lab_id
                LIMIT 2
                """
            )
            mentors = cur.fetchall()
        if len(mentors) < 2:
            raise unittest.SkipTest("数据库缺少足够的导师数据")
        cls.mentors = mentors

    def setUp(self):
        self.start_time = datetime.now() - timedelta(seconds=5)
        self.admission_ids = []
        suffix = str(time.time_ns())[-9:]
        self.account_ids = []
        self.student_ids = []
        with self.conn.cursor() as cur:
            for tag in ("A", "B"):
                cur.execute(
                    """
                    INSERT INTO account (user_no, user_type, login_name, password_hash, status)
                    VALUES (%s, 'STUDENT', %s, 'x', 'ACTIVE')
                    """,
                    (f"UT{suffix}{tag}", f"ut_{suffix}_{tag}"),
                )
                account_id = cur.lastrowid
                cur.execute(
                    """
                    INSERT INTO student (account_id, student_no, name, selection_state)
                    VALUES (%s, %s, %s, 'NOT_FILLED')
                    """,
                    (account_id, f"UT{suffix}{tag}", f"单测学生{tag}"),
                )
                self.account_ids.append(account_id)
                self.student_ids.append(cur.lastrowid)
            cur.execute("SELECT COALESCE(MAX(round_no), 0) + 1 AS n FROM selection_round")
            round_no = cur.fetchone()["n"]
            now = datetime.now()
            cur.execute(
                """
                INSERT INTO selection_round
                  (round_no, round_type, round_name, intent_limit, fill_start, fill_end,
                   interview_start, interview_end, confirm_start, confirm_end, status)
                VALUES (%s, 'THIRD', %s, 1, %s, %s, %s, %s, %s, %s, 'FILLING')
                """,
                (
                    round_no,
                    f"单测-关闭轮次-{suffix}",
                    now - timedelta(hours=1),
                    now + timedelta(hours=1),
                    now - timedelta(hours=1),
                    now + timedelta(hours=2),
                    now,
                    now + timedelta(hours=12),
                ),
            )
            self.round_id = cur.lastrowid
            self.round_no = round_no
        self.extra_round_ids = []
        self.suspended_rounds = []
        self.session_ids = []
        self.conn.commit()

    def tearDown(self):
        with self.conn.cursor() as cur:
            cur.execute(
                "DELETE FROM notification WHERE receiver_account_id IN (%s, %s)",
                tuple(self.account_ids),
            )
            cur.execute(
                """
                DELETE FROM audit_log
                WHERE created_at >= %s
                  AND action_code IN ('ADMISSION_AUTO_CONFIRM', 'ADMISSION_CONFIRM_REMIND',
                                      'ADMISSION_RELEASE_EXPIRED', 'ADMISSION_RELEASE_ROUND_OPEN',
                                      'STUDENT_ENTER_SUPPLEMENT', 'INTERVIEW_SESSION_CANCEL')
                  AND biz_id IN ({})
                """.format(",".join(["%s"] * (2 + len(self.admission_ids) + len(self.session_ids)))),
                (self.start_time, *self.student_ids, *self.admission_ids, *self.session_ids),
            )
            if self.session_ids:
                placeholders = ",".join(["%s"] * len(self.session_ids))
                for table in ("interview_reminder", "interview_participant", "interview_judge"):
                    cur.execute(
                        f"DELETE FROM {table} WHERE session_id IN ({placeholders})",
                        tuple(self.session_ids),
                    )
                cur.execute(
                    f"DELETE FROM interview_session WHERE session_id IN ({placeholders})",
                    tuple(self.session_ids),
                )
            if self.admission_ids:
                placeholders = ",".join(["%s"] * len(self.admission_ids))
                cur.execute(
                    f"DELETE FROM lab_quota_log WHERE admission_id IN ({placeholders})",
                    tuple(self.admission_ids),
                )
                cur.execute(
                    f"DELETE FROM admission WHERE admission_id IN ({placeholders})",
                    tuple(self.admission_ids),
                )
            cur.execute("DELETE FROM selection_round WHERE round_id = %s", (self.round_id,))
            if self.extra_round_ids:
                placeholders = ",".join(["%s"] * len(self.extra_round_ids))
                cur.execute(
                    f"DELETE FROM selection_round WHERE round_id IN ({placeholders})",
                    tuple(self.extra_round_ids),
                )
            cur.execute(
                "DELETE FROM student WHERE student_id IN (%s, %s)", tuple(self.student_ids)
            )
            cur.execute(
                "DELETE FROM account WHERE account_id IN (%s, %s)", tuple(self.account_ids)
            )
        self.conn.commit()

    # ---------------- 辅助 ----------------

    def add_admission(self, student_id, mentor):
        with self.conn.cursor() as cur:
            cur.execute(
                """
                INSERT INTO admission
                  (student_id, lab_id, mentor_id, round_id, source_type, confirm_type,
                   status, confirm_deadline, reason)
                VALUES (%s, %s, %s, %s, 'ROUND3', 'MANUAL', 'OCCUPIED',
                        DATE_ADD(NOW(3), INTERVAL 12 HOUR), '单测')
                """,
                (student_id, mentor["lab_id"], mentor["mentor_id"], self.round_id),
            )
            admission_id = cur.lastrowid
        self.conn.commit()
        self.admission_ids.append(admission_id)
        return admission_id

    def admission_row(self, admission_id):
        with self.conn.cursor() as cur:
            cur.execute(
                """
                SELECT admission_id, status, confirm_type, confirm_deadline,
                       TIMESTAMPDIFF(MINUTE, NOW(3), confirm_deadline) AS remain_minutes
                FROM admission WHERE admission_id = %s
                """,
                (admission_id,),
            )
            return cur.fetchone()

    def student_state(self, student_id):
        with self.conn.cursor() as cur:
            cur.execute(
                "SELECT selection_state FROM student WHERE student_id = %s", (student_id,)
            )
            return cur.fetchone()["selection_state"]

    def audit_rows(self, admission_id, action_code="ADMISSION_AUTO_CONFIRM"):
        with self.conn.cursor() as cur:
            cur.execute(
                """
                SELECT action_code, after_value FROM audit_log
                WHERE biz_type = 'ADMISSION' AND biz_id = %s
                  AND action_code = %s AND created_at >= %s
                """,
                (admission_id, action_code, self.start_time),
            )
            return cur.fetchall()

    def notices(self, account_id, titles):
        placeholders = ",".join(["%s"] * len(titles))
        with self.conn.cursor() as cur:
            cur.execute(
                f"""
                SELECT title, content FROM notification
                WHERE receiver_account_id = %s AND title IN ({placeholders})
                """,
                (account_id, *titles),
            )
            return cur.fetchall()

    def suspend_real_rounds(self):
        """临时移走演示轮次，避免“开放轮次释放未确认名额”影响真实数据。"""
        with self.conn.cursor() as cur:
            cur.execute(
                "SELECT round_id, round_no, status FROM selection_round WHERE round_id <> %s",
                (self.round_id,),
            )
            self.suspended_rounds = cur.fetchall()
            cur.execute(
                "UPDATE selection_round SET round_no = round_no + 50, status = 'PLANNED' "
                "WHERE round_id <> %s",
                (self.round_id,),
            )
        self.conn.commit()

    def resume_suspended_rounds(self):
        with self.conn.cursor() as cur:
            for row in self.suspended_rounds:
                cur.execute(
                    "UPDATE selection_round SET round_no = %s, status = %s WHERE round_id = %s",
                    (row["round_no"], row["status"], row["round_id"]),
                )
        self.conn.commit()
        self.suspended_rounds = []

    def create_next_round(self):
        now = datetime.now()
        with self.conn.cursor() as cur:
            cur.execute(
                """
                INSERT INTO selection_round
                  (round_no, round_type, round_name, intent_limit, fill_start, fill_end,
                   interview_start, interview_end, confirm_start, confirm_end, status)
                VALUES (%s, 'THIRD', %s, 1, %s, %s, %s, %s, %s, %s, 'PLANNED')
                """,
                (
                    self.round_no + 1,
                    "单测-下一轮次",
                    now,
                    now + timedelta(days=1),
                    now,
                    now + timedelta(days=2),
                    now,
                    now + timedelta(days=2),
                ),
            )
            new_round_id = cur.lastrowid
        self.conn.commit()
        self.extra_round_ids.append(new_round_id)
        return new_round_id

    # ---------------- 用例 ----------------

    def test_single_admission_auto_confirmed(self):
        """仅 1 个拟录取时，轮次关闭自动确认。"""
        admission_id = self.add_admission(self.student_ids[0], self.mentors[0])

        result = filing_api.close_round_api(self.round_id, self.coordinator)

        self.assertEqual(result["roundId"], self.round_id)
        self.assertEqual(result["status"], "CLOSED")
        self.assertEqual(result["autoConfirmed"], 1)
        self.assertEqual(result["reminded"], 0)
        row = self.admission_row(admission_id)
        self.assertEqual(row["status"], "CONFIRMED")
        self.assertEqual(row["confirm_type"], "AUTO")
        self.assertEqual(self.student_state(self.student_ids[0]), "CONFIRMED")
        notices = self.notices(self.account_ids[0], ("拟录取已自动确认",))
        self.assertEqual(len(notices), 1)

    def test_multiple_admissions_reminded_with_30_minutes(self):
        """2~3 个拟录取时发送提醒，并把确认截止设为 30 分钟后。"""
        first = self.add_admission(self.student_ids[1], self.mentors[0])
        second = self.add_admission(self.student_ids[1], self.mentors[1])

        result = filing_api.close_round_api(self.round_id, self.coordinator)

        self.assertEqual(result["autoConfirmed"], 0)
        self.assertEqual(result["reminded"], 1)
        self.assertEqual(result["confirmDeadlineMinutes"], 30)
        for admission_id in (first, second):
            row = self.admission_row(admission_id)
            self.assertEqual(row["status"], "OCCUPIED")
            self.assertGreaterEqual(row["remain_minutes"], 28)
            self.assertLessEqual(row["remain_minutes"], 30)
        self.assertEqual(self.student_state(self.student_ids[1]), "PENDING_CONFIRM")
        notices = self.notices(self.account_ids[1], ("拟录取待确认提醒",))
        self.assertEqual(len(notices), 1)
        self.assertIn("30 分钟", notices[0]["content"])
        self.assertIn("补录", notices[0]["content"])

    def test_unconfirmed_released_and_enter_supplement(self):
        """30 分钟未确认：释放全部名额并进入补录。"""
        first = self.add_admission(self.student_ids[1], self.mentors[0])
        second = self.add_admission(self.student_ids[1], self.mentors[1])
        filing_api.close_round_api(self.round_id, self.coordinator)

        with self.conn.cursor() as cur:
            cur.execute(
                """
                UPDATE admission SET confirm_deadline = DATE_SUB(NOW(3), INTERVAL 1 MINUTE)
                WHERE admission_id IN (%s, %s)
                """,
                (first, second),
            )
        self.conn.commit()

        result = filing_api.release_expired_admissions()

        self.assertIn("supplementStudents", result)
        # 若后台到期扫描抢先释放，本次调用可能返回 0；最终状态一致即可
        if result["released"]:
            self.assertGreaterEqual(result["released"], 2)
            self.assertIn(self.student_ids[1], result["supplementStudents"])
        for admission_id in (first, second):
            self.assertEqual(self.admission_row(admission_id)["status"], "RELEASED")
        self.assertEqual(self.student_state(self.student_ids[1]), "SUPPLEMENT")
        notices = self.notices(self.account_ids[1], ("已进入补录",))
        self.assertEqual(len(notices), 1)
        self.assertIn("补录", notices[0]["content"])

    def test_close_round_without_proposals(self):
        """无拟录取的轮次关闭不产生副作用。"""
        result = filing_api.close_round_api(self.round_id, self.coordinator)

        self.assertEqual(result["status"], "CLOSED")
        self.assertEqual(result["autoConfirmed"], 0)
        self.assertEqual(result["reminded"], 0)
        self.assertEqual(self.student_state(self.student_ids[0]), "NOT_FILLED")
        self.assertEqual(self.student_state(self.student_ids[1]), "NOT_FILLED")

    def test_open_next_round_auto_confirms_single_proposal(self):
        """下一轮开放时，仅一份拟录取的学生未确认则自动确认。"""
        admission_id = self.add_admission(self.student_ids[0], self.mentors[0])
        self.suspend_real_rounds()
        new_round_id = self.create_next_round()
        try:
            result = filing_api.open_round_api(new_round_id, self.coordinator)
        finally:
            self.resume_suspended_rounds()

        self.assertEqual(result["autoConfirmed"], 1)
        self.assertIn(admission_id, result["autoConfirmedIds"])
        self.assertEqual(result["released"], 0)
        self.assertEqual(result["supplementStudents"], [])
        row = self.admission_row(admission_id)
        self.assertEqual(row["status"], "CONFIRMED")
        self.assertEqual(row["confirm_type"], "AUTO")
        self.assertEqual(self.student_state(self.student_ids[0]), "CONFIRMED")
        notices = self.notices(self.account_ids[0], ("拟录取已自动确认",))
        self.assertEqual(len(notices), 1)
        self.assertIn("唯一一份拟录取结果", notices[0]["content"])
        audits = self.audit_rows(admission_id)
        self.assertEqual(len(audits), 1)
        self.assertIn('"trigger": "ROUND_OPEN"', audits[0]["after_value"])

    def test_open_next_round_mixed_students(self):
        """同一批次中：唯一拟录取自动确认、多份拟录取释放，互不影响。"""
        single = self.add_admission(self.student_ids[0], self.mentors[0])
        multi_first = self.add_admission(self.student_ids[1], self.mentors[0])
        multi_second = self.add_admission(self.student_ids[1], self.mentors[1])
        self.suspend_real_rounds()
        new_round_id = self.create_next_round()
        try:
            result = filing_api.open_round_api(new_round_id, self.coordinator)
        finally:
            self.resume_suspended_rounds()

        self.assertEqual(result["autoConfirmed"], 1)
        self.assertEqual(result["autoConfirmedIds"], [single])
        self.assertGreaterEqual(result["released"], 2)
        self.assertEqual(len(result["supplementStudents"]), 1)
        self.assertIn(self.student_ids[1], result["supplementStudents"])

        self.assertEqual(self.admission_row(single)["status"], "CONFIRMED")
        self.assertEqual(self.student_state(self.student_ids[0]), "CONFIRMED")
        for admission_id in (multi_first, multi_second):
            self.assertEqual(self.admission_row(admission_id)["status"], "RELEASED")
        self.assertEqual(self.student_state(self.student_ids[1]), "SUPPLEMENT")
        # 自动确认的学生不应再收到释放/补录通知
        self.assertEqual(len(self.notices(self.account_ids[0], ("上一轮拟录取已释放", "已进入补录"))), 0)

    def test_open_next_round_keeps_confirmed_admission(self):
        """已确认的拟录取不属于待处理范围，开启新一轮时保持不变。"""
        admission_id = self.add_admission(self.student_ids[0], self.mentors[0])
        with self.conn.cursor() as cur:
            cur.execute(
                """
                UPDATE admission
                SET status = 'CONFIRMED', confirm_type = 'MANUAL', confirmed_at = NOW(3),
                    reason = '学生已手动确认'
                WHERE admission_id = %s
                """,
                (admission_id,),
            )
        self.conn.commit()

        self.suspend_real_rounds()
        new_round_id = self.create_next_round()
        try:
            result = filing_api.open_round_api(new_round_id, self.coordinator)
        finally:
            self.resume_suspended_rounds()

        self.assertEqual(result["autoConfirmed"], 0)
        self.assertEqual(result["released"], 0)
        self.assertEqual(result["supplementStudents"], [])
        row = self.admission_row(admission_id)
        self.assertEqual(row["status"], "CONFIRMED")
        self.assertEqual(row["confirm_type"], "MANUAL")
        self.assertEqual(self.student_state(self.student_ids[0]), "NOT_FILLED")
        self.assertEqual(len(self.audit_rows(admission_id)), 0)

    def test_open_next_round_releases_multiple_unconfirmed(self):
        """下一轮开放时，多份未确认拟录取自动释放并进入补录。"""
        first = self.add_admission(self.student_ids[1], self.mentors[0])
        second = self.add_admission(self.student_ids[1], self.mentors[1])
        self.suspend_real_rounds()
        new_round_id = self.create_next_round()
        try:
            result = filing_api.open_round_api(new_round_id, self.coordinator)
        finally:
            self.resume_suspended_rounds()

        self.assertEqual(result["autoConfirmed"], 0)
        self.assertGreaterEqual(result["released"], 2)
        self.assertIn(self.student_ids[1], result["supplementStudents"])
        for admission_id in (first, second):
            self.assertEqual(self.admission_row(admission_id)["status"], "RELEASED")
        self.assertEqual(self.student_state(self.student_ids[1]), "SUPPLEMENT")
        release_notices = self.notices(self.account_ids[1], ("上一轮拟录取已释放",))
        self.assertEqual(len(release_notices), 2)
        supplement_notices = self.notices(self.account_ids[1], ("已进入补录",))
        self.assertEqual(len(supplement_notices), 1)

    def test_open_next_round_cancels_previous_sessions(self):
        """进入新一轮次后，此前轮次未评分的面试场次自动撤销。"""
        now = datetime.now()
        with self.conn.cursor() as cur:
            cur.execute(
                """
                INSERT INTO interview_session
                  (round_id, lab_id, session_name, start_time, end_time, location,
                   organizer_teacher_id, status)
                VALUES (%s, %s, %s, %s, %s, '单测教室', %s, 'PLANNED')
                """,
                (
                    self.round_id,
                    self.mentors[0]["lab_id"],
                    "单测-旧面试场次",
                    now + timedelta(days=1),
                    now + timedelta(days=1, hours=1),
                    self.coordinator["teacher_id"],
                ),
            )
            session_id = cur.lastrowid
        self.conn.commit()
        self.session_ids.append(session_id)

        self.suspend_real_rounds()
        new_round_id = self.create_next_round()
        try:
            result = filing_api.open_round_api(new_round_id, self.coordinator)
        finally:
            self.resume_suspended_rounds()

        self.assertGreaterEqual(result["canceledSessions"], 1)
        self.assertIn(session_id, result["canceledSessionIds"])
        self.assertNotIn(session_id, result["keptScoredSessionIds"])
        with self.conn.cursor() as cur:
            cur.execute(
                "SELECT status FROM interview_session WHERE session_id = %s", (session_id,)
            )
            self.assertEqual(cur.fetchone()["status"], "CANCELED")


if __name__ == "__main__":
    unittest.main(verbosity=2)
