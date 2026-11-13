-- =========================================================
-- 实训室选组选拔系统 初始化数据脚本
-- 依据：数据库设计文档 v1.0 / 项目需求说明书
-- 适用：空库首次初始化（数据库 sxsxb）
-- 说明：
--   1. 日程以脚本执行日（D 日）为基准动态生成，意向填报窗口覆盖 D 日至 D+2 日；
--      如需固定届次日期，可将 TIMESTAMP(...) 表达式替换为具体日期。
--   2. 演示账号密码统一为 123456，password_hash 存 SHA-256 无盐摘要；
--      生产环境请改为 bcrypt / Argon2 并逐账号加盐。
--   3. 本脚本为一次性初始化，请勿在已有业务数据的库中重复执行。
-- =========================================================

SET NAMES utf8mb4;
START TRANSACTION;

SET @d = CURDATE();  -- D 日：脚本执行当天

-- ---------------------------------------------------------
-- 1. 评分方案（四维度等权，百分制，至少 2 名评委）
-- ---------------------------------------------------------
INSERT INTO score_scheme
  (scheme_id, scheme_name, round_type, learning_weight, logic_weight,
   programming_weight, time_weight, min_score, max_score,
   judge_count_min, calc_mode, status, effective_from)
VALUES
  (1, '学院统一评分方案', NULL, 25.00, 25.00, 25.00, 25.00,
   0.00, 100.00, 2, 'AVG', 'ACTIVE', '2026-10-29 00:00:00');

-- ---------------------------------------------------------
-- 2. 选拔阶段（8 个阶段，按 D 日相对排期）
-- ---------------------------------------------------------
INSERT INTO selection_stage
  (stage_no, stage_code, stage_name, start_time, end_time, status)
VALUES
  (1, 'BRIEFING',  '集中宣讲',        TIMESTAMP(DATE_SUB(@d, INTERVAL 7 DAY), '14:00:00'), TIMESTAMP(DATE_SUB(@d, INTERVAL 7 DAY), '16:00:00'), 'CLOSED'),
  (2, 'OPEN_DAY',  '实训室开放日',    TIMESTAMP(DATE_SUB(@d, INTERVAL 3 DAY), '09:00:00'), TIMESTAMP(DATE_SUB(@d, INTERVAL 2 DAY), '17:00:00'), 'CLOSED'),
  (3, 'INTENT',    '意向填报',        TIMESTAMP(@d, '00:00:00'),                           TIMESTAMP(DATE_ADD(@d, INTERVAL 2 DAY), '23:59:59'), 'OPEN'),
  (4, 'INTERVIEW', '导师初选与面试考核', TIMESTAMP(DATE_ADD(@d, INTERVAL 3 DAY), '08:00:00'), TIMESTAMP(DATE_ADD(@d, INTERVAL 4 DAY), '17:00:00'), 'PLANNED'),
  (5, 'CONFIRM',   '拟录取与学生确认',  TIMESTAMP(DATE_ADD(@d, INTERVAL 5 DAY), '08:00:00'), TIMESTAMP(DATE_ADD(@d, INTERVAL 5 DAY), '12:00:00'), 'PLANNED'),
  (6, 'SUPPLEMENT','分轮补录',        TIMESTAMP(DATE_ADD(@d, INTERVAL 5 DAY), '12:00:00'), TIMESTAMP(DATE_ADD(@d, INTERVAL 6 DAY), '17:00:00'), 'PLANNED'),
  (7, 'ADJUST',    '统筹调剂录取',     TIMESTAMP(DATE_ADD(@d, INTERVAL 7 DAY), '08:00:00'), TIMESTAMP(DATE_ADD(@d, INTERVAL 7 DAY), '12:00:00'), 'PLANNED'),
  (8, 'PUBLICITY', '结果公示与正式入组', TIMESTAMP(DATE_ADD(@d, INTERVAL 7 DAY), '12:00:00'), TIMESTAMP(DATE_ADD(@d, INTERVAL 8 DAY), '12:00:00'), 'PLANNED');

-- ---------------------------------------------------------
-- 3. 选拔轮次（首轮 3 个意向；补录每轮 1 个；统筹调剂不填报）
-- ---------------------------------------------------------
INSERT INTO selection_round
  (round_no, round_type, round_name, intent_limit,
   fill_start, fill_end, interview_start, interview_end, confirm_start, confirm_end, status)
VALUES
  (1, 'FIRST',  '首轮选报', 3,
   TIMESTAMP(@d, '00:00:00'), TIMESTAMP(DATE_ADD(@d, INTERVAL 2 DAY), '23:59:59'),
   TIMESTAMP(DATE_ADD(@d, INTERVAL 3 DAY), '08:00:00'), TIMESTAMP(DATE_ADD(@d, INTERVAL 4 DAY), '17:00:00'),
   TIMESTAMP(DATE_ADD(@d, INTERVAL 5 DAY), '08:00:00'), TIMESTAMP(DATE_ADD(@d, INTERVAL 5 DAY), '12:00:00'), 'FILLING'),
  (2, 'SECOND', '第二轮补录', 1,
   TIMESTAMP(DATE_ADD(@d, INTERVAL 5 DAY), '12:00:00'), TIMESTAMP(DATE_ADD(@d, INTERVAL 5 DAY), '15:00:00'),
   TIMESTAMP(DATE_ADD(@d, INTERVAL 5 DAY), '15:00:00'), TIMESTAMP(DATE_ADD(@d, INTERVAL 5 DAY), '21:00:00'),
   TIMESTAMP(DATE_ADD(@d, INTERVAL 6 DAY), '08:00:00'), TIMESTAMP(DATE_ADD(@d, INTERVAL 6 DAY), '11:00:00'), 'PLANNED'),
  (3, 'THIRD',  '第三轮补录', 1,
   TIMESTAMP(DATE_ADD(@d, INTERVAL 6 DAY), '11:00:00'), TIMESTAMP(DATE_ADD(@d, INTERVAL 6 DAY), '13:00:00'),
   TIMESTAMP(DATE_ADD(@d, INTERVAL 6 DAY), '13:00:00'), TIMESTAMP(DATE_ADD(@d, INTERVAL 6 DAY), '15:00:00'),
   TIMESTAMP(DATE_ADD(@d, INTERVAL 6 DAY), '15:00:00'), TIMESTAMP(DATE_ADD(@d, INTERVAL 6 DAY), '17:00:00'), 'PLANNED'),
  (4, 'ADJUST', '统筹调剂', 0,
   TIMESTAMP(DATE_ADD(@d, INTERVAL 7 DAY), '08:00:00'), TIMESTAMP(DATE_ADD(@d, INTERVAL 7 DAY), '12:00:00'),
   TIMESTAMP(DATE_ADD(@d, INTERVAL 7 DAY), '08:00:00'), TIMESTAMP(DATE_ADD(@d, INTERVAL 7 DAY), '12:00:00'),
   TIMESTAMP(DATE_ADD(@d, INTERVAL 7 DAY), '08:00:00'), TIMESTAMP(DATE_ADD(@d, INTERVAL 7 DAY), '12:00:00'), 'PLANNED');

-- ---------------------------------------------------------
-- 4. 实训室（名额为硬上限）
-- ---------------------------------------------------------
INSERT INTO lab
  (lab_id, lab_code, name, college, direction, quota, training_plan, assessment_requirement, status)
VALUES
  (1, 'L01', 'AI 应用实训室', '信息工程学院',
   'AI 应用开发与大模型实践', 12,
   '以 AI+PBL 项目制培养，围绕大模型应用、智能体、计算机视觉开展 12 周项目实训。',
   '按项目里程碑与结项答辩考核，重视学习态度、逻辑思维、编程基础与时间投入。', 'OPEN'),
  (2, 'L02', '智能硬件实训室', '信息工程学院',
   '嵌入式与智能硬件开发', 10,
   '以嵌入式系统、机器人、物联网为主线开展软硬件协同项目实训。',
   '按硬件调试记录、项目演示与答辩考核。', 'OPEN'),
  (3, 'L03', '数据智能实训室', '信息工程学院',
   '数据分析与机器学习工程', 10,
   '围绕数据采集、清洗、建模、可视化与推荐系统开展项目实训。',
   '按数据项目交付物、模型效果与答辩考核。', 'OPEN'),
  (4, 'L04', '全栈开发实训室', '信息工程学院',
   '全栈工程与云原生实践', 8,
   '覆盖前端、后端、数据库与容器化部署的完整工程链路实训。',
   '按迭代交付、代码质量与结项答辩考核。', 'OPEN');

-- ---------------------------------------------------------
-- 5. 登录账号（老师 12 个 + 学生 10 个，密码统一 123456）
-- ---------------------------------------------------------
INSERT INTO account
  (account_id, user_no, user_type, login_name, password_hash, status)
VALUES
  (1,  'T001',    'TEACHER', 'T001',    SHA2('123456', 256), 'ACTIVE'),
  (2,  'T002',    'TEACHER', 'T002',    SHA2('123456', 256), 'ACTIVE'),
  (3,  'T003',    'TEACHER', 'T003',    SHA2('123456', 256), 'ACTIVE'),
  (4,  'T004',    'TEACHER', 'T004',    SHA2('123456', 256), 'ACTIVE'),
  (5,  'T005',    'TEACHER', 'T005',    SHA2('123456', 256), 'ACTIVE'),
  (6,  'T006',    'TEACHER', 'T006',    SHA2('123456', 256), 'ACTIVE'),
  (7,  'T007',    'TEACHER', 'T007',    SHA2('123456', 256), 'ACTIVE'),
  (8,  'T008',    'TEACHER', 'T008',    SHA2('123456', 256), 'ACTIVE'),
  (9,  'T009',    'TEACHER', 'T009',    SHA2('123456', 256), 'ACTIVE'),
  (10, 'T010',    'TEACHER', 'T010',    SHA2('123456', 256), 'ACTIVE'),
  (11, 'T011',    'TEACHER', 'T011',    SHA2('123456', 256), 'ACTIVE'),
  (12, 'T012',    'TEACHER', 'T012',    SHA2('123456', 256), 'ACTIVE'),
  (13, 'S2026001','STUDENT', 'S2026001',SHA2('123456', 256), 'ACTIVE'),
  (14, 'S2026002','STUDENT', 'S2026002',SHA2('123456', 256), 'ACTIVE'),
  (15, 'S2026003','STUDENT', 'S2026003',SHA2('123456', 256), 'ACTIVE'),
  (16, 'S2026004','STUDENT', 'S2026004',SHA2('123456', 256), 'ACTIVE'),
  (17, 'S2026005','STUDENT', 'S2026005',SHA2('123456', 256), 'ACTIVE'),
  (18, 'S2026006','STUDENT', 'S2026006',SHA2('123456', 256), 'ACTIVE'),
  (19, 'S2026007','STUDENT', 'S2026007',SHA2('123456', 256), 'ACTIVE'),
  (20, 'S2026008','STUDENT', 'S2026008',SHA2('123456', 256), 'ACTIVE'),
  (21, 'S2026009','STUDENT', 'S2026009',SHA2('123456', 256), 'ACTIVE'),
  (22, 'S2026010','STUDENT', 'S2026010',SHA2('123456', 256), 'ACTIVE');

-- ---------------------------------------------------------
-- 6. 老师
-- ---------------------------------------------------------
INSERT INTO teacher
  (teacher_id, account_id, teacher_no, name, college, title, status)
VALUES
  (1,  1,  'T001', '李明', '信息工程学院', '副教授',     'ACTIVE'),
  (2,  2,  'T002', '王芳', '信息工程学院', '讲师',       'ACTIVE'),
  (3,  3,  'T003', '张涛', '信息工程学院', '副教授',     'ACTIVE'),
  (4,  4,  'T004', '刘敏', '信息工程学院', '高级工程师', 'ACTIVE'),
  (5,  5,  'T005', '陈晨', '信息工程学院', '讲师',       'ACTIVE'),
  (6,  6,  'T006', '赵鹏', '信息工程学院', '讲师',       'ACTIVE'),
  (7,  7,  'T007', '孙丽', '信息工程学院', '讲师',       'ACTIVE'),
  (8,  8,  'T008', '周斌', '信息工程学院', '高级工程师', 'ACTIVE'),
  (9,  9,  'T009', '吴迪', '信息工程学院', '讲师',       'ACTIVE'),
  (10, 10, 'T010', '郑霞', '信息工程学院', '副教授',     'ACTIVE'),
  (11, 11, 'T011', '王磊', '信息工程学院', '讲师',       'ACTIVE'),
  (12, 12, 'T012', '李静', '信息工程学院', '讲师',       'ACTIVE');

-- ---------------------------------------------------------
-- 7. 导师（每室 3 人，含负责人）
-- ---------------------------------------------------------
INSERT INTO mentor
  (mentor_id, teacher_id, lab_id, research_direction, capacity, status)
VALUES
  (1,  1,  1, '大模型应用与智能体', 4, 'ACTIVE'),
  (2,  5,  1, '计算机视觉',         3, 'ACTIVE'),
  (3,  6,  1, '自然语言处理',       3, 'ACTIVE'),
  (4,  2,  2, '嵌入式与机器人',     4, 'ACTIVE'),
  (5,  7,  2, '物联网与传感',       3, 'ACTIVE'),
  (6,  8,  2, '智能硬件设计',       3, 'ACTIVE'),
  (7,  3,  3, '数据挖掘与推荐系统', 4, 'ACTIVE'),
  (8,  9,  3, '数据分析与可视化',   3, 'ACTIVE'),
  (9,  10, 3, '机器学习平台',       3, 'ACTIVE'),
  (10, 4,  4, '全栈工程与云原生',   3, 'ACTIVE'),
  (11, 11, 4, '前端与移动开发',     3, 'ACTIVE'),
  (12, 12, 4, '后端与微服务',       3, 'ACTIVE');

-- ---------------------------------------------------------
-- 8. 老师岗位（导师 / 实训室负责人 / 全院统筹岗）
-- ---------------------------------------------------------
INSERT INTO teacher_role
  (teacher_id, role_code, scope_lab_id, status, granted_by)
VALUES
  (1,  'COORDINATOR', 0, 'ACTIVE', 1),
  (1,  'LAB_OWNER',   1, 'ACTIVE', 1),
  (2,  'LAB_OWNER',   2, 'ACTIVE', 1),
  (3,  'LAB_OWNER',   3, 'ACTIVE', 1),
  (4,  'LAB_OWNER',   4, 'ACTIVE', 1),
  (1,  'MENTOR', 1, 'ACTIVE', 1),
  (5,  'MENTOR', 1, 'ACTIVE', 1),
  (6,  'MENTOR', 1, 'ACTIVE', 1),
  (2,  'MENTOR', 2, 'ACTIVE', 1),
  (7,  'MENTOR', 2, 'ACTIVE', 1),
  (8,  'MENTOR', 2, 'ACTIVE', 1),
  (3,  'MENTOR', 3, 'ACTIVE', 1),
  (9,  'MENTOR', 3, 'ACTIVE', 1),
  (10, 'MENTOR', 3, 'ACTIVE', 1),
  (4,  'MENTOR', 4, 'ACTIVE', 1),
  (11, 'MENTOR', 4, 'ACTIVE', 1),
  (12, 'MENTOR', 4, 'ACTIVE', 1);

-- ---------------------------------------------------------
-- 9. 学生（演示数据，状态均为未填报）
-- ---------------------------------------------------------
INSERT INTO student
  (student_id, account_id, student_no, name, gender, grade, major, college,
   programming_base, available_hours, selection_state)
VALUES
  (1,  13, 'S2026001', '张伟', '男', '2024', '计算机科学与技术',     '信息工程学院', 'GOOD',  16.0, 'NOT_FILLED'),
  (2,  14, 'S2026002', '李娜', '女', '2024', '软件工程',             '信息工程学院', 'BASIC', 12.0, 'NOT_FILLED'),
  (3,  15, 'S2026003', '王强', '男', '2024', '人工智能',             '信息工程学院', 'GOOD',  18.0, 'NOT_FILLED'),
  (4,  16, 'S2026004', '刘洋', '男', '2024', '计算机科学与技术',     '信息工程学院', 'BASIC', 10.0, 'NOT_FILLED'),
  (5,  17, 'S2026005', '陈静', '女', '2024', '数据科学与大数据技术', '信息工程学院', 'GOOD',  14.0, 'NOT_FILLED'),
  (6,  18, 'S2026006', '赵磊', '男', '2024', '软件工程',             '信息工程学院', 'BASIC', 12.0, 'NOT_FILLED'),
  (7,  19, 'S2026007', '孙悦', '女', '2024', '人工智能',             '信息工程学院', 'GOOD',  20.0, 'NOT_FILLED'),
  (8,  20, 'S2026008', '周涛', '男', '2024', '网络工程',             '信息工程学院', 'BASIC', 10.0, 'NOT_FILLED'),
  (9,  21, 'S2026009', '吴敏', '女', '2024', '数据科学与大数据技术', '信息工程学院', 'GOOD',  16.0, 'NOT_FILLED'),
  (10, 22, 'S2026010', '郑浩', '男', '2024', '计算机科学与技术',     '信息工程学院', 'BASIC', 12.0, 'NOT_FILLED');

COMMIT;

-- ---------------------------------------------------------
-- 10. 初始化结果自检（可选，执行后可查看）
-- ---------------------------------------------------------
-- SELECT 'score_scheme' AS t, COUNT(*) AS c FROM score_scheme
-- UNION ALL SELECT 'selection_stage', COUNT(*) FROM selection_stage
-- UNION ALL SELECT 'selection_round', COUNT(*) FROM selection_round
-- UNION ALL SELECT 'lab', COUNT(*) FROM lab
-- UNION ALL SELECT 'account', COUNT(*) FROM account
-- UNION ALL SELECT 'teacher', COUNT(*) FROM teacher
-- UNION ALL SELECT 'mentor', COUNT(*) FROM mentor
-- UNION ALL SELECT 'teacher_role', COUNT(*) FROM teacher_role
-- UNION ALL SELECT 'student', COUNT(*) FROM student;
