-- =========================================================
-- 实训室选组选拔系统 初始化数据脚本
-- 依据：数据库设计文档 v1.0 / 项目需求说明书
-- 适用：空库首次初始化（数据库 sxsxb）
-- 说明：
--   1. 日程以脚本执行日（D 日）为基准动态生成，D 日即意向填报日；相对关系按流程图：
--      宣讲 D-4 14:00；开放日 D-4~D-3；意向填报 D 08:00-12:00；面试 D 12:00~D+1 17:00；
--      拟录取确认 D+2 08:00-12:00；分轮补录 D+2 12:00~D+3 17:00；统筹调剂 D+4 08:00-12:00；
--      公示 D+4 12:00 起 1 天。轮次时间窗嵌入对应阶段。
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
  (1, 'BRIEFING',  '集中宣讲',        TIMESTAMP(DATE_SUB(@d, INTERVAL 4 DAY), '14:00:00'), TIMESTAMP(DATE_SUB(@d, INTERVAL 4 DAY), '16:00:00'), 'CLOSED'),
  (2, 'OPEN_DAY',  '实训室开放日',    TIMESTAMP(DATE_SUB(@d, INTERVAL 4 DAY), '14:00:00'), TIMESTAMP(DATE_SUB(@d, INTERVAL 3 DAY), '17:00:00'), 'CLOSED'),
  (3, 'INTENT',    '意向填报',        TIMESTAMP(@d, '08:00:00'),                           TIMESTAMP(@d, '12:00:00'),                           'OPEN'),
  (4, 'INTERVIEW', '导师初选与面试考核', TIMESTAMP(@d, '12:00:00'),                         TIMESTAMP(DATE_ADD(@d, INTERVAL 1 DAY), '17:00:00'), 'PLANNED'),
  (5, 'CONFIRM',   '拟录取与学生确认',  TIMESTAMP(DATE_ADD(@d, INTERVAL 2 DAY), '08:00:00'), TIMESTAMP(DATE_ADD(@d, INTERVAL 2 DAY), '12:00:00'), 'PLANNED'),
  (6, 'SUPPLEMENT','分轮补录',        TIMESTAMP(DATE_ADD(@d, INTERVAL 2 DAY), '12:00:00'), TIMESTAMP(DATE_ADD(@d, INTERVAL 3 DAY), '17:00:00'), 'PLANNED'),
  (7, 'ADJUST',    '统筹调剂录取',     TIMESTAMP(DATE_ADD(@d, INTERVAL 4 DAY), '08:00:00'), TIMESTAMP(DATE_ADD(@d, INTERVAL 4 DAY), '12:00:00'), 'PLANNED'),
  (8, 'PUBLICITY', '结果公示与正式入组', TIMESTAMP(DATE_ADD(@d, INTERVAL 4 DAY), '12:00:00'), TIMESTAMP(DATE_ADD(@d, INTERVAL 5 DAY), '12:00:00'), 'PLANNED');

-- ---------------------------------------------------------
-- 3. 选拔轮次（首轮 3 个意向；补录每轮 1 个；统筹调剂不填报）
-- ---------------------------------------------------------
INSERT INTO selection_round
  (round_no, round_type, round_name, intent_limit,
   fill_start, fill_end, interview_start, interview_end, confirm_start, confirm_end, status)
VALUES
  (1, 'FIRST',  '首轮选报', 3,
   TIMESTAMP(@d, '08:00:00'), TIMESTAMP(@d, '12:00:00'),
   TIMESTAMP(@d, '12:00:00'), TIMESTAMP(DATE_ADD(@d, INTERVAL 1 DAY), '17:00:00'),
   TIMESTAMP(DATE_ADD(@d, INTERVAL 2 DAY), '08:00:00'), TIMESTAMP(DATE_ADD(@d, INTERVAL 2 DAY), '12:00:00'), 'PLANNED'),
  (2, 'SECOND', '第二轮补录', 1,
   TIMESTAMP(DATE_ADD(@d, INTERVAL 2 DAY), '12:00:00'), TIMESTAMP(DATE_ADD(@d, INTERVAL 3 DAY), '09:00:00'),
   TIMESTAMP(DATE_ADD(@d, INTERVAL 3 DAY), '09:00:00'), TIMESTAMP(DATE_ADD(@d, INTERVAL 3 DAY), '12:00:00'),
   TIMESTAMP(DATE_ADD(@d, INTERVAL 3 DAY), '12:00:00'), TIMESTAMP(DATE_ADD(@d, INTERVAL 3 DAY), '14:00:00'), 'PLANNED'),
  (3, 'THIRD',  '第三轮补录', 1,
   TIMESTAMP(DATE_ADD(@d, INTERVAL 3 DAY), '14:00:00'), TIMESTAMP(DATE_ADD(@d, INTERVAL 3 DAY), '15:00:00'),
   TIMESTAMP(DATE_ADD(@d, INTERVAL 3 DAY), '15:00:00'), TIMESTAMP(DATE_ADD(@d, INTERVAL 3 DAY), '16:00:00'),
   TIMESTAMP(DATE_ADD(@d, INTERVAL 3 DAY), '16:00:00'), TIMESTAMP(DATE_ADD(@d, INTERVAL 3 DAY), '17:00:00'), 'PLANNED'),
  (4, 'ADJUST', '统筹调剂', 0,
   TIMESTAMP(DATE_ADD(@d, INTERVAL 4 DAY), '08:00:00'), TIMESTAMP(DATE_ADD(@d, INTERVAL 4 DAY), '12:00:00'),
   TIMESTAMP(DATE_ADD(@d, INTERVAL 4 DAY), '08:00:00'), TIMESTAMP(DATE_ADD(@d, INTERVAL 4 DAY), '11:00:00'),
   TIMESTAMP(DATE_ADD(@d, INTERVAL 4 DAY), '11:00:00'), TIMESTAMP(DATE_ADD(@d, INTERVAL 4 DAY), '12:00:00'), 'PLANNED');

-- ---------------------------------------------------------
-- 4. 实训室（名额为硬上限）
-- ---------------------------------------------------------
INSERT INTO lab
  (lab_id, lab_code, name, college, direction, intro, quota, training_plan, assessment_requirement, status)
VALUES
  (1, 'L01', 'AI 应用实训室', '信息工程学院',
   'AI 应用开发与大模型实践',
   '面向大模型与智能体应用方向，配备 GPU 训练服务器、AIGC 应用开发平台与校企合作数据集。以 AI+PBL 项目制组织实训，围绕智能问答、RAG 检索增强、多智能体协作与计算机视觉开展 12 周项目实践，注重从需求分析、方案设计到部署上线的完整工程闭环。', 12,
   '以 AI+PBL 项目制培养，围绕大模型应用、智能体、计算机视觉开展 12 周项目实训。',
   '按项目里程碑与结项答辩考核，重视学习态度、逻辑思维、编程基础与时间投入。', 'OPEN'),
  (2, 'L02', '智能硬件实训室', '信息工程学院',
   '嵌入式与智能硬件开发',
   '面向嵌入式与智能硬件开发，配备嵌入式开发板、传感器套件、机器人平台与硬件调试工位。实训围绕智能车、环境监测与智能家居原型展开，覆盖电路设计、嵌入式编程、传感器采集与整机联调，强调动手实践与安全规范。', 10,
   '以嵌入式系统、机器人、物联网为主线开展软硬件协同项目实训。',
   '按硬件调试记录、项目演示与答辩考核。', 'OPEN'),
  (3, 'L03', '数据智能实训室', '信息工程学院',
   '数据分析与机器学习工程',
   '面向数据分析与机器学习工程，配备数据分析工作站、模型训练环境与可视化大屏。实训围绕数据采集、清洗、特征工程、建模评估与推荐系统展开，引导学生完成从数据到结论的完整项目交付，突出模型效果与业务价值。', 10,
   '围绕数据采集、清洗、建模、可视化与推荐系统开展项目实训。',
   '按数据项目交付物、模型效果与答辩考核。', 'OPEN'),
  (4, 'L04', '全栈开发实训室', '信息工程学院',
   '全栈工程与云原生实践',
   '面向全栈工程与云原生实践，配备前后端开发环境、数据库服务与容器化部署集群。实训覆盖需求拆解、接口设计、数据库建模、前端交互与容器部署，以迭代交付方式培养完整工程链路能力，强调代码质量与团队协作。', 8,
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
  (teacher_id, account_id, teacher_no, name, college, title, intro, status)
VALUES
  (1,  1,  'T001', '李明', '信息工程学院', '副教授',
   '研究方向为大模型应用与智能体编排，主讲《人工智能导论》《大模型应用开发》。主持校企合作智能问答与知识库项目，指导学生完成多智能体协作、RAG 检索增强等实训课题，注重工程落地与项目复盘。', 'ACTIVE'),
  (2,  2,  'T002', '王芳', '信息工程学院', '讲师',
   '研究方向为嵌入式系统与移动机器人，主讲《嵌入式开发》《机器人控制基础》。指导学生参加智能车与机器人大赛，擅长将传感器、电机控制与嵌入式编程融入项目实训，重视动手调试与安全规范。', 'ACTIVE'),
  (3,  3,  'T003', '张涛', '信息工程学院', '副教授',
   '研究方向为数据挖掘与推荐系统，主讲《数据挖掘》《推荐系统实践》。主持用户行为分析与个性化推荐相关课题，带领学生完成从数据清洗、特征工程到模型上线的完整数据项目。', 'ACTIVE'),
  (4,  4,  'T004', '刘敏', '信息工程学院', '高级工程师',
   '曾任职互联网企业后端架构师，研究方向为全栈工程与云原生。主讲《Web 全栈开发》《容器与 DevOps》，以真实业务迭代方式组织实训，强调代码规范、接口设计与持续交付。', 'ACTIVE'),
  (5,  5,  'T005', '陈晨', '信息工程学院', '讲师',
   '研究方向为计算机视觉与图像处理，主讲《数字图像处理》《深度学习实践》。指导学生开展目标检测、图像分割与视觉巡检项目，注重数据集构建、实验对比与结果可视化。', 'ACTIVE'),
  (6,  6,  'T006', '赵鹏', '信息工程学院', '讲师',
   '研究方向为自然语言处理与文本挖掘，主讲《自然语言处理》《Python 数据分析》。参与舆情分析与文本分类项目，指导学生在中文分词、语义表示与提示工程方向开展实训。', 'ACTIVE'),
  (7,  7,  'T007', '孙丽', '信息工程学院', '讲师',
   '研究方向为物联网与传感网络，主讲《物联网技术》《无线传感网》。指导学生搭建环境监测与智能家居原型，覆盖传感采集、协议通信、边缘网关到可视化展示的完整链路。', 'ACTIVE'),
  (8,  8,  'T008', '周斌', '信息工程学院', '高级工程师',
   '具有多年智能硬件产品研发经验，研究方向为智能硬件设计。主讲《硬件电路设计》《产品原型开发》，带学生完成从需求分析、原理图与 PCB 到样机调试的全流程实训。', 'ACTIVE'),
  (9,  9,  'T009', '吴迪', '信息工程学院', '讲师',
   '研究方向为数据分析与可视化，主讲《数据可视化》《商业数据分析》。指导学生使用 BI 工具与前端图表库完成数据看板项目，强调指标口径、叙事表达与交互体验。', 'ACTIVE'),
  (10, 10, 'T010', '郑霞', '信息工程学院', '副教授',
   '研究方向为机器学习平台与工程化，主讲《机器学习》《MLOps 实践》。主持模型训练与部署平台建设项目，指导学生完成特征管理、模型评估与上线监控的工程化实训。', 'ACTIVE'),
  (11, 11, 'T011', '王磊', '信息工程学院', '讲师',
   '研究方向为前端与移动应用开发，主讲《Web 前端开发》《跨端应用实践》。指导学生在组件化、响应式布局与性能优化方向开展项目实训，注重代码可维护性与用户体验。', 'ACTIVE'),
  (12, 12, 'T012', '李静', '信息工程学院', '讲师',
   '研究方向为后端与微服务架构，主讲《Java 程序设计》《微服务架构》。指导学生完成 RESTful 接口设计、数据库建模与服务拆分项目，强调接口规范、异常处理与测试覆盖。', 'ACTIVE');

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
