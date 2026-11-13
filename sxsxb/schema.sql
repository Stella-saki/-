-- 实训室选组选拔系统 建表脚本
-- 依据：数据库设计文档 v1.0（MySQL 8.0+）
-- 说明：不启用外键约束，关系由应用层与索引保证

SET NAMES utf8mb4;
SET FOREIGN_KEY_CHECKS = 0;

-- =========================================================
-- 实训室选组选拔系统 DDL（MySQL 8.0+）
-- 说明：不强制外键，关系由应用层与索引保证
-- =========================================================

CREATE TABLE account (
  account_id     BIGINT UNSIGNED NOT NULL AUTO_INCREMENT COMMENT '账号主键',
  user_no        VARCHAR(32)  NOT NULL COMMENT '学号/工号',
  user_type      VARCHAR(16)  NOT NULL COMMENT 'STUDENT/TEACHER',
  login_name     VARCHAR(64)  NOT NULL COMMENT '登录名',
  password_hash  VARCHAR(128) NOT NULL COMMENT '密码摘要',
  status         VARCHAR(16)  NOT NULL DEFAULT 'ACTIVE' COMMENT 'ACTIVE/DISABLED',
  created_at     DATETIME(3)  NOT NULL DEFAULT CURRENT_TIMESTAMP(3),
  updated_at     DATETIME(3)  NOT NULL DEFAULT CURRENT_TIMESTAMP(3) ON UPDATE CURRENT_TIMESTAMP(3),
  PRIMARY KEY (account_id),
  UNIQUE KEY uk_account_user_no (user_no),
  UNIQUE KEY uk_account_login_name (login_name)
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COLLATE=utf8mb4_0900_ai_ci COMMENT='登录账号';

CREATE TABLE student (
  student_id        BIGINT UNSIGNED NOT NULL AUTO_INCREMENT,
  account_id        BIGINT UNSIGNED NOT NULL COMMENT '关联账号',
  student_no        VARCHAR(32)  NOT NULL COMMENT '学号',
  name              VARCHAR(50)  NOT NULL COMMENT '姓名',
  gender            VARCHAR(8)   NULL COMMENT '性别',
  grade             VARCHAR(16)  NULL COMMENT '年级',
  major             VARCHAR(64)  NULL COMMENT '专业',
  college           VARCHAR(64)  NULL COMMENT '学院',
  programming_base  VARCHAR(32)  NULL COMMENT '编程基础 NONE/BASIC/GOOD',
  available_hours   DECIMAL(4,1) NULL COMMENT '每周可投入小时',
  phone             VARCHAR(32)  NULL,
  email             VARCHAR(64)  NULL,
  grade_rank        VARCHAR(32)  NULL COMMENT '年级排名，如 3/120（注册必填，历史数据可空）',
  awards            VARCHAR(500) NULL COMMENT '获奖情况',
  specialty         VARCHAR(200) NULL COMMENT '擅长方向',
  dev_direction     VARCHAR(200) NULL COMMENT '意向发展方向',
  selection_state   VARCHAR(32)  NOT NULL DEFAULT 'NOT_FILLED' COMMENT '选拔状态汇总',
  created_at        DATETIME(3)  NOT NULL DEFAULT CURRENT_TIMESTAMP(3),
  updated_at        DATETIME(3)  NOT NULL DEFAULT CURRENT_TIMESTAMP(3) ON UPDATE CURRENT_TIMESTAMP(3),
  PRIMARY KEY (student_id),
  UNIQUE KEY uk_student_no (student_no),
  UNIQUE KEY uk_student_account (account_id),
  KEY idx_student_state (selection_state)
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COLLATE=utf8mb4_0900_ai_ci COMMENT='学生';

CREATE TABLE teacher (
  teacher_id   BIGINT UNSIGNED NOT NULL AUTO_INCREMENT,
  account_id   BIGINT UNSIGNED NOT NULL COMMENT '关联账号',
  teacher_no   VARCHAR(32) NOT NULL COMMENT '工号',
  name         VARCHAR(50) NOT NULL COMMENT '姓名',
  college      VARCHAR(64) NULL COMMENT '所属学院',
  title        VARCHAR(32) NULL COMMENT '职称',
  intro        VARCHAR(500) NULL COMMENT '教师简介',
  phone        VARCHAR(32) NULL,
  email        VARCHAR(64) NULL,
  status       VARCHAR(16) NOT NULL DEFAULT 'ACTIVE' COMMENT 'ACTIVE/DISABLED',
  created_at   DATETIME(3) NOT NULL DEFAULT CURRENT_TIMESTAMP(3),
  updated_at   DATETIME(3) NOT NULL DEFAULT CURRENT_TIMESTAMP(3) ON UPDATE CURRENT_TIMESTAMP(3),
  PRIMARY KEY (teacher_id),
  UNIQUE KEY uk_teacher_no (teacher_no),
  UNIQUE KEY uk_teacher_account (account_id)
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COLLATE=utf8mb4_0900_ai_ci COMMENT='老师';

CREATE TABLE teacher_role (
  role_id       BIGINT UNSIGNED NOT NULL AUTO_INCREMENT,
  teacher_id    BIGINT UNSIGNED NOT NULL,
  role_code     VARCHAR(16) NOT NULL COMMENT 'MENTOR/LAB_OWNER/COORDINATOR',
  scope_lab_id  BIGINT UNSIGNED NOT NULL DEFAULT 0 COMMENT '作用实训室，统筹岗为0',
  status        VARCHAR(16) NOT NULL DEFAULT 'ACTIVE' COMMENT 'ACTIVE/REVOKED',
  granted_by    BIGINT UNSIGNED NOT NULL COMMENT '授权人',
  granted_at    DATETIME(3) NOT NULL DEFAULT CURRENT_TIMESTAMP(3),
  PRIMARY KEY (role_id),
  UNIQUE KEY uk_teacher_role (teacher_id, role_code, scope_lab_id),
  KEY idx_role_lab (scope_lab_id, role_code, status)
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COLLATE=utf8mb4_0900_ai_ci COMMENT='老师岗位';

CREATE TABLE lab (
  lab_id                 BIGINT UNSIGNED NOT NULL AUTO_INCREMENT,
  lab_code               VARCHAR(32) NOT NULL COMMENT '实训室编号',
  name                   VARCHAR(64) NOT NULL COMMENT '名称',
  college                VARCHAR(64) NULL COMMENT '所属学院',
  direction              VARCHAR(255) NULL COMMENT '项目方向',
  intro                  VARCHAR(1000) NULL COMMENT '实训室简介',
  quota                  INT NOT NULL COMMENT '招录名额（硬上限）',
  training_plan          TEXT NULL COMMENT '培养安排',
  assessment_requirement TEXT NULL COMMENT '考核要求',
  status                 VARCHAR(16) NOT NULL DEFAULT 'DRAFT' COMMENT 'DRAFT/OPEN/CLOSED',
  created_at             DATETIME(3) NOT NULL DEFAULT CURRENT_TIMESTAMP(3),
  updated_at             DATETIME(3) NOT NULL DEFAULT CURRENT_TIMESTAMP(3) ON UPDATE CURRENT_TIMESTAMP(3),
  PRIMARY KEY (lab_id),
  UNIQUE KEY uk_lab_code (lab_code),
  KEY idx_lab_status (status)
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COLLATE=utf8mb4_0900_ai_ci COMMENT='实训室';

CREATE TABLE mentor (
  mentor_id          BIGINT UNSIGNED NOT NULL AUTO_INCREMENT,
  teacher_id         BIGINT UNSIGNED NOT NULL,
  lab_id             BIGINT UNSIGNED NOT NULL,
  research_direction VARCHAR(255) NULL COMMENT '研究方向',
  capacity           INT NULL COMMENT '可带人数（软约束）',
  status             VARCHAR(16) NOT NULL DEFAULT 'ACTIVE' COMMENT 'ACTIVE/INACTIVE',
  created_at         DATETIME(3) NOT NULL DEFAULT CURRENT_TIMESTAMP(3),
  updated_at         DATETIME(3) NOT NULL DEFAULT CURRENT_TIMESTAMP(3) ON UPDATE CURRENT_TIMESTAMP(3),
  PRIMARY KEY (mentor_id),
  UNIQUE KEY uk_mentor_teacher_lab (teacher_id, lab_id),
  KEY idx_mentor_lab (lab_id, status)
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COLLATE=utf8mb4_0900_ai_ci COMMENT='导师';

CREATE TABLE score_scheme (
  scheme_id          BIGINT UNSIGNED NOT NULL AUTO_INCREMENT,
  scheme_name        VARCHAR(64) NOT NULL COMMENT '方案名称',
  round_type         VARCHAR(16) NULL COMMENT '适用轮次，NULL 为全局',
  learning_weight    DECIMAL(5,2) NOT NULL COMMENT '学习态度权重',
  logic_weight       DECIMAL(5,2) NOT NULL COMMENT '逻辑思维权重',
  programming_weight DECIMAL(5,2) NOT NULL COMMENT '编程基础权重',
  time_weight        DECIMAL(5,2) NOT NULL COMMENT '时间投入权重',
  min_score          DECIMAL(5,2) NOT NULL COMMENT '维度最低分',
  max_score          DECIMAL(5,2) NOT NULL COMMENT '维度最高分',
  judge_count_min    TINYINT NOT NULL DEFAULT 2 COMMENT '最少评委数',
  calc_mode          VARCHAR(16) NOT NULL DEFAULT 'AVG' COMMENT 'AVG/TRIM_AVG/WEIGHTED',
  status             VARCHAR(16) NOT NULL DEFAULT 'ACTIVE' COMMENT 'ACTIVE/INACTIVE',
  effective_from     DATETIME(3) NULL,
  PRIMARY KEY (scheme_id),
  KEY idx_scheme_round (round_type, status)
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COLLATE=utf8mb4_0900_ai_ci COMMENT='评分方案';

CREATE TABLE selection_stage (
  stage_id   BIGINT UNSIGNED NOT NULL AUTO_INCREMENT,
  stage_no   TINYINT NOT NULL COMMENT '阶段序号 1~8',
  stage_code VARCHAR(32) NOT NULL COMMENT '阶段编码',
  stage_name VARCHAR(64) NOT NULL COMMENT '阶段名称',
  start_time DATETIME(3) NOT NULL COMMENT '开始时间',
  end_time   DATETIME(3) NOT NULL COMMENT '结束时间',
  status     VARCHAR(16) NOT NULL DEFAULT 'PLANNED' COMMENT 'PLANNED/OPEN/CLOSED/EARLY_CLOSED',
  opened_by  BIGINT UNSIGNED NULL,
  opened_at  DATETIME(3) NULL,
  closed_by  BIGINT UNSIGNED NULL,
  closed_at  DATETIME(3) NULL,
  remark     VARCHAR(255) NULL COMMENT '提前开关原因',
  PRIMARY KEY (stage_id),
  UNIQUE KEY uk_stage_no (stage_no),
  KEY idx_stage_time (start_time, end_time)
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COLLATE=utf8mb4_0900_ai_ci COMMENT='选拔阶段';

CREATE TABLE selection_round (
  round_id        BIGINT UNSIGNED NOT NULL AUTO_INCREMENT,
  round_no        TINYINT NOT NULL COMMENT '1首轮/2二轮/3三轮/4统筹调剂',
  round_type      VARCHAR(16) NOT NULL COMMENT 'FIRST/SECOND/THIRD/ADJUST',
  round_name      VARCHAR(64) NOT NULL COMMENT '轮次名称',
  intent_limit    TINYINT NOT NULL COMMENT '意向上限：首轮3，补录1',
  fill_start      DATETIME(3) NOT NULL,
  fill_end        DATETIME(3) NOT NULL,
  interview_start DATETIME(3) NOT NULL,
  interview_end   DATETIME(3) NOT NULL,
  confirm_start   DATETIME(3) NOT NULL,
  confirm_end     DATETIME(3) NOT NULL,
  status          VARCHAR(16) NOT NULL DEFAULT 'PLANNED' COMMENT 'PLANNED/FILLING/INTERVIEWING/CONFIRMING/CLOSED',
  created_at      DATETIME(3) NOT NULL DEFAULT CURRENT_TIMESTAMP(3),
  updated_at      DATETIME(3) NOT NULL DEFAULT CURRENT_TIMESTAMP(3) ON UPDATE CURRENT_TIMESTAMP(3),
  PRIMARY KEY (round_id),
  UNIQUE KEY uk_round_no (round_no),
  KEY idx_round_time (fill_start, fill_end)
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COLLATE=utf8mb4_0900_ai_ci COMMENT='选拔轮次';

CREATE TABLE intent_application (
  application_id         BIGINT UNSIGNED NOT NULL AUTO_INCREMENT,
  round_id               BIGINT UNSIGNED NOT NULL,
  student_id             BIGINT UNSIGNED NOT NULL,
  accept_internal_adjust TINYINT(1) NOT NULL DEFAULT 0 COMMENT '服从实训室内部调剂',
  accept_cross_adjust    TINYINT(1) NOT NULL DEFAULT 0 COMMENT '服从全院跨实训室调剂',
  intent_count           TINYINT NOT NULL DEFAULT 0 COMMENT '已填意向数',
  status                 VARCHAR(16) NOT NULL DEFAULT 'DRAFT' COMMENT 'DRAFT/SUBMITTED',
  submitted_at           DATETIME(3) NULL,
  created_at             DATETIME(3) NOT NULL DEFAULT CURRENT_TIMESTAMP(3),
  updated_at             DATETIME(3) NOT NULL DEFAULT CURRENT_TIMESTAMP(3) ON UPDATE CURRENT_TIMESTAMP(3),
  PRIMARY KEY (application_id),
  UNIQUE KEY uk_application_round_student (round_id, student_id),
  KEY idx_application_student (student_id)
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COLLATE=utf8mb4_0900_ai_ci COMMENT='意向申请单';

CREATE TABLE intent (
  intent_id      BIGINT UNSIGNED NOT NULL AUTO_INCREMENT,
  application_id BIGINT UNSIGNED NOT NULL,
  round_id       BIGINT UNSIGNED NOT NULL,
  student_id     BIGINT UNSIGNED NOT NULL,
  mentor_id      BIGINT UNSIGNED NOT NULL,
  lab_id         BIGINT UNSIGNED NOT NULL,
  parallel_no    TINYINT NOT NULL COMMENT '平行序号1~3，补录固定1',
  status         VARCHAR(32) NOT NULL DEFAULT 'DRAFT' COMMENT '见枚举 5.2',
  submitted_at   DATETIME(3) NULL,
  created_at     DATETIME(3) NOT NULL DEFAULT CURRENT_TIMESTAMP(3),
  updated_at     DATETIME(3) NOT NULL DEFAULT CURRENT_TIMESTAMP(3) ON UPDATE CURRENT_TIMESTAMP(3),
  PRIMARY KEY (intent_id),
  UNIQUE KEY uk_intent_app_parallel (application_id, parallel_no),
  UNIQUE KEY uk_intent_app_mentor (application_id, mentor_id),
  KEY idx_intent_lab_status (lab_id, status),
  KEY idx_intent_student_round (student_id, round_id, status),
  KEY idx_intent_mentor (mentor_id)
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COLLATE=utf8mb4_0900_ai_ci COMMENT='平行意向';

CREATE TABLE interview_session (
  session_id           BIGINT UNSIGNED NOT NULL AUTO_INCREMENT,
  round_id             BIGINT UNSIGNED NOT NULL,
  lab_id               BIGINT UNSIGNED NOT NULL,
  session_name         VARCHAR(64) NULL,
  start_time           DATETIME(3) NOT NULL,
  end_time             DATETIME(3) NOT NULL,
  location             VARCHAR(128) NULL,
  organizer_teacher_id BIGINT UNSIGNED NOT NULL COMMENT '组织人（实训室负责人）',
  status               VARCHAR(16) NOT NULL DEFAULT 'PLANNED' COMMENT 'PLANNED/ONGOING/FINISHED/CANCELED',
  remark               VARCHAR(255) NULL,
  created_at           DATETIME(3) NOT NULL DEFAULT CURRENT_TIMESTAMP(3),
  updated_at           DATETIME(3) NOT NULL DEFAULT CURRENT_TIMESTAMP(3) ON UPDATE CURRENT_TIMESTAMP(3),
  PRIMARY KEY (session_id),
  KEY idx_session_lab_round (lab_id, round_id, status),
  KEY idx_session_time (start_time)
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COLLATE=utf8mb4_0900_ai_ci COMMENT='面试场次';

CREATE TABLE interview_judge (
  judge_id   BIGINT UNSIGNED NOT NULL AUTO_INCREMENT,
  session_id BIGINT UNSIGNED NOT NULL,
  teacher_id BIGINT UNSIGNED NOT NULL,
  judge_role VARCHAR(16) NOT NULL DEFAULT 'JUDGE' COMMENT 'CHAIR/JUDGE',
  created_at DATETIME(3) NOT NULL DEFAULT CURRENT_TIMESTAMP(3),
  PRIMARY KEY (judge_id),
  UNIQUE KEY uk_judge_session_teacher (session_id, teacher_id)
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COLLATE=utf8mb4_0900_ai_ci COMMENT='场次评委';

CREATE TABLE interview_participant (
  participant_id BIGINT UNSIGNED NOT NULL AUTO_INCREMENT,
  session_id     BIGINT UNSIGNED NOT NULL,
  intent_id      BIGINT UNSIGNED NOT NULL,
  student_id     BIGINT UNSIGNED NOT NULL,
  checkin_status VARCHAR(16) NOT NULL DEFAULT 'NOT_CHECKED_IN' COMMENT 'NOT_CHECKED_IN/CHECKED_IN/ABSENT',
  checkin_at     DATETIME(3) NULL,
  created_at     DATETIME(3) NOT NULL DEFAULT CURRENT_TIMESTAMP(3),
  updated_at     DATETIME(3) NOT NULL DEFAULT CURRENT_TIMESTAMP(3) ON UPDATE CURRENT_TIMESTAMP(3),
  PRIMARY KEY (participant_id),
  UNIQUE KEY uk_participant_session_intent (session_id, intent_id),
  KEY idx_participant_intent (intent_id),
  KEY idx_participant_student (session_id, student_id)
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COLLATE=utf8mb4_0900_ai_ci COMMENT='场次学生';

CREATE TABLE interview_reminder (
  reminder_id    BIGINT UNSIGNED NOT NULL AUTO_INCREMENT,
  session_id     BIGINT UNSIGNED NOT NULL,
  intent_id      BIGINT UNSIGNED NOT NULL,
  participant_id BIGINT UNSIGNED NOT NULL,
  student_id     BIGINT UNSIGNED NOT NULL,
  offset_minutes INT NOT NULL COMMENT '提前分钟数：1440/120',
  remind_at      DATETIME(3) NOT NULL COMMENT '计划提醒时间',
  channel        VARCHAR(16) NOT NULL DEFAULT 'SITE' COMMENT 'SITE/SMS/WECHAT',
  send_status    VARCHAR(16) NOT NULL DEFAULT 'PENDING' COMMENT 'PENDING/SENT/FAILED/CANCELED',
  retry_count    TINYINT NOT NULL DEFAULT 0,
  fail_reason    VARCHAR(255) NULL,
  sent_at        DATETIME(3) NULL,
  read_at        DATETIME(3) NULL,
  ack_at         DATETIME(3) NULL COMMENT '学生回执已知晓时间',
  created_at     DATETIME(3) NOT NULL DEFAULT CURRENT_TIMESTAMP(3),
  updated_at     DATETIME(3) NOT NULL DEFAULT CURRENT_TIMESTAMP(3) ON UPDATE CURRENT_TIMESTAMP(3),
  PRIMARY KEY (reminder_id),
  UNIQUE KEY uk_reminder_session_intent_offset (session_id, intent_id, offset_minutes),
  KEY idx_reminder_dispatch (send_status, remind_at),
  KEY idx_reminder_student (student_id)
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COLLATE=utf8mb4_0900_ai_ci COMMENT='面试提醒记录';

CREATE TABLE interview_score (
  score_id          BIGINT UNSIGNED NOT NULL AUTO_INCREMENT,
  session_id        BIGINT UNSIGNED NOT NULL,
  intent_id         BIGINT UNSIGNED NOT NULL,
  student_id        BIGINT UNSIGNED NOT NULL,
  judge_teacher_id  BIGINT UNSIGNED NOT NULL,
  learning_score    DECIMAL(5,2) NOT NULL COMMENT '学习态度',
  logic_score       DECIMAL(5,2) NOT NULL COMMENT '逻辑思维',
  programming_score DECIMAL(5,2) NOT NULL COMMENT '编程基础',
  time_score        DECIMAL(5,2) NOT NULL COMMENT '时间投入',
  total_score       DECIMAL(5,2) NOT NULL COMMENT '个人总分',
  comment           VARCHAR(1000) NULL,
  status            VARCHAR(16) NOT NULL DEFAULT 'DRAFT' COMMENT 'DRAFT/SUBMITTED',
  submitted_at      DATETIME(3) NULL,
  created_at        DATETIME(3) NOT NULL DEFAULT CURRENT_TIMESTAMP(3),
  updated_at        DATETIME(3) NOT NULL DEFAULT CURRENT_TIMESTAMP(3) ON UPDATE CURRENT_TIMESTAMP(3),
  PRIMARY KEY (score_id),
  UNIQUE KEY uk_score_session_intent_judge (session_id, intent_id, judge_teacher_id),
  KEY idx_score_intent (intent_id),
  KEY idx_score_student (student_id)
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COLLATE=utf8mb4_0900_ai_ci COMMENT='面试评分';

CREATE TABLE selection_result (
  result_id   BIGINT UNSIGNED NOT NULL AUTO_INCREMENT,
  round_id    BIGINT UNSIGNED NOT NULL,
  lab_id      BIGINT UNSIGNED NOT NULL,
  student_id  BIGINT UNSIGNED NOT NULL,
  intent_id   BIGINT UNSIGNED NULL COMMENT '关联意向；统筹调剂可能无有效意向',
  list_type   VARCHAR(24) NOT NULL COMMENT 'PROPOSED/WAITLIST/INTERNAL_CANDIDATE/REJECTED',
  rank_no     INT NULL COMMENT '顺位',
  score       DECIMAL(5,2) NULL COMMENT '汇总得分',
  generated_by BIGINT UNSIGNED NOT NULL,
  generated_at DATETIME(3) NOT NULL DEFAULT CURRENT_TIMESTAMP(3),
  remark      VARCHAR(255) NULL,
  PRIMARY KEY (result_id),
  UNIQUE KEY uk_result_round_lab_student_list (round_id, lab_id, student_id, list_type),
  KEY idx_result_rank (round_id, lab_id, list_type, rank_no)
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COLLATE=utf8mb4_0900_ai_ci COMMENT='选拔结论';

CREATE TABLE admission (
  admission_id      BIGINT UNSIGNED NOT NULL AUTO_INCREMENT,
  student_id        BIGINT UNSIGNED NOT NULL,
  lab_id            BIGINT UNSIGNED NOT NULL,
  mentor_id         BIGINT UNSIGNED NOT NULL,
  round_id          BIGINT UNSIGNED NOT NULL,
  source_type       VARCHAR(24) NOT NULL COMMENT 'ROUND1/ROUND2/ROUND3/ADJUST_INTERNAL/ADJUST_CROSS/NEGOTIATED',
  confirm_type      VARCHAR(8) NOT NULL COMMENT 'AUTO/MANUAL',
  status            VARCHAR(16) NOT NULL DEFAULT 'OCCUPIED' COMMENT 'OCCUPIED/CONFIRMED/EFFECTIVE/ENROLLED/ABANDONED/RELEASED',
  occupied_at       DATETIME(3) NOT NULL DEFAULT CURRENT_TIMESTAMP(3),
  confirm_deadline  DATETIME(3) NULL,
  confirmed_at      DATETIME(3) NULL,
  released_at       DATETIME(3) NULL,
  reason            VARCHAR(500) NULL,
  active_student_id BIGINT UNSIGNED GENERATED ALWAYS AS (
                      CASE WHEN status IN ('CONFIRMED','EFFECTIVE','ENROLLED')
                           THEN student_id ELSE NULL END) STORED COMMENT '已确认录取学生ID（OCCUPIED 不占用，允许多条待确认拟录取）',
  created_at        DATETIME(3) NOT NULL DEFAULT CURRENT_TIMESTAMP(3),
  updated_at        DATETIME(3) NOT NULL DEFAULT CURRENT_TIMESTAMP(3) ON UPDATE CURRENT_TIMESTAMP(3),
  PRIMARY KEY (admission_id),
  UNIQUE KEY uk_admission_active_student (active_student_id),
  KEY idx_admission_lab_status (lab_id, status),
  KEY idx_admission_student (student_id),
  KEY idx_admission_round (round_id)
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COLLATE=utf8mb4_0900_ai_ci COMMENT='录取记录';

CREATE TABLE lab_quota_log (
  log_id              BIGINT UNSIGNED NOT NULL AUTO_INCREMENT,
  lab_id              BIGINT UNSIGNED NOT NULL,
  round_id            BIGINT UNSIGNED NOT NULL,
  change_type         VARCHAR(16) NOT NULL COMMENT 'OCCUPY/RELEASE/ADJUST_IN/ADJUST_OUT',
  delta               TINYINT NOT NULL COMMENT '+1/-1',
  active_count_before INT NOT NULL,
  active_count_after  INT NOT NULL,
  admission_id        BIGINT UNSIGNED NULL,
  operator_id         BIGINT UNSIGNED NULL COMMENT '系统操作记0',
  remark              VARCHAR(255) NULL,
  created_at          DATETIME(3) NOT NULL DEFAULT CURRENT_TIMESTAMP(3),
  PRIMARY KEY (log_id),
  KEY idx_quota_lab_round (lab_id, round_id, created_at),
  KEY idx_quota_admission (admission_id)
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COLLATE=utf8mb4_0900_ai_ci COMMENT='名额变动流水';

CREATE TABLE adjustment (
  adjust_id     BIGINT UNSIGNED NOT NULL AUTO_INCREMENT,
  round_id      BIGINT UNSIGNED NOT NULL,
  student_id    BIGINT UNSIGNED NOT NULL,
  priority_level TINYINT NOT NULL COMMENT '1内部/2跨室/3约谈统筹',
  adjust_type   VARCHAR(16) NOT NULL COMMENT 'INTERNAL/CROSS/NEGOTIATED',
  from_lab_id   BIGINT UNSIGNED NULL,
  to_lab_id     BIGINT UNSIGNED NULL,
  to_mentor_id  BIGINT UNSIGNED NULL,
  status        VARCHAR(16) NOT NULL DEFAULT 'PENDING' COMMENT 'PENDING/MATCHED/REJECTED/ABANDONED',
  admission_id  BIGINT UNSIGNED NULL,
  operator_id   BIGINT UNSIGNED NOT NULL COMMENT '系统自动匹配记0',
  decided_at    DATETIME(3) NULL,
  remark        VARCHAR(500) NULL,
  created_at    DATETIME(3) NOT NULL DEFAULT CURRENT_TIMESTAMP(3),
  updated_at    DATETIME(3) NOT NULL DEFAULT CURRENT_TIMESTAMP(3) ON UPDATE CURRENT_TIMESTAMP(3),
  PRIMARY KEY (adjust_id),
  KEY idx_adjust_student (student_id),
  KEY idx_adjust_to_lab (to_lab_id, status),
  KEY idx_adjust_priority (round_id, priority_level, status)
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COLLATE=utf8mb4_0900_ai_ci COMMENT='调剂记录';

CREATE TABLE negotiation (
  negotiation_id        BIGINT UNSIGNED NOT NULL AUTO_INCREMENT,
  round_id              BIGINT UNSIGNED NOT NULL,
  student_id            BIGINT UNSIGNED NOT NULL,
  interviewer_teacher_id BIGINT UNSIGNED NOT NULL COMMENT '约谈人',
  negotiate_time        DATETIME(3) NOT NULL COMMENT '约谈时间',
  method                VARCHAR(16) NOT NULL COMMENT 'FACE_TO_FACE/PHONE/ONLINE',
  willingness           VARCHAR(500) NULL COMMENT '学生意愿',
  conclusion            VARCHAR(16) NOT NULL COMMENT 'ACCEPT/REJECT',
  final_lab_id          BIGINT UNSIGNED NULL,
  final_mentor_id       BIGINT UNSIGNED NULL,
  admission_id          BIGINT UNSIGNED NULL,
  remark                VARCHAR(500) NULL,
  recorded_at           DATETIME(3) NOT NULL DEFAULT CURRENT_TIMESTAMP(3),
  created_at            DATETIME(3) NOT NULL DEFAULT CURRENT_TIMESTAMP(3),
  PRIMARY KEY (negotiation_id),
  KEY idx_negotiation_round_student (round_id, student_id),
  KEY idx_negotiation_student (student_id)
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COLLATE=utf8mb4_0900_ai_ci COMMENT='约谈记录';

CREATE TABLE publicity_batch (
  batch_id     BIGINT UNSIGNED NOT NULL AUTO_INCREMENT,
  round_id     BIGINT UNSIGNED NULL,
  batch_no     VARCHAR(32) NOT NULL,
  start_time   DATETIME(3) NOT NULL,
  end_time     DATETIME(3) NOT NULL,
  status       VARCHAR(16) NOT NULL DEFAULT 'PUBLISHING' COMMENT 'PUBLISHING/FINISHED/EFFECTIVE',
  published_by BIGINT UNSIGNED NOT NULL,
  published_at DATETIME(3) NOT NULL DEFAULT CURRENT_TIMESTAMP(3),
  remark       VARCHAR(255) NULL,
  created_at   DATETIME(3) NOT NULL DEFAULT CURRENT_TIMESTAMP(3),
  updated_at   DATETIME(3) NOT NULL DEFAULT CURRENT_TIMESTAMP(3) ON UPDATE CURRENT_TIMESTAMP(3),
  PRIMARY KEY (batch_id),
  UNIQUE KEY uk_publicity_batch_no (batch_no)
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COLLATE=utf8mb4_0900_ai_ci COMMENT='公示批次';

CREATE TABLE publicity_item (
  item_id     BIGINT UNSIGNED NOT NULL AUTO_INCREMENT,
  batch_id    BIGINT UNSIGNED NOT NULL,
  student_id  BIGINT UNSIGNED NOT NULL,
  lab_id      BIGINT UNSIGNED NOT NULL,
  mentor_id   BIGINT UNSIGNED NOT NULL,
  admission_id BIGINT UNSIGNED NOT NULL,
  item_status VARCHAR(16) NOT NULL DEFAULT 'NORMAL' COMMENT 'NORMAL/SUSPENDED/REVOKED',
  created_at  DATETIME(3) NOT NULL DEFAULT CURRENT_TIMESTAMP(3),
  updated_at  DATETIME(3) NOT NULL DEFAULT CURRENT_TIMESTAMP(3) ON UPDATE CURRENT_TIMESTAMP(3),
  PRIMARY KEY (item_id),
  UNIQUE KEY uk_publicity_item_batch_student (batch_id, student_id),
  KEY idx_publicity_item_lab (batch_id, lab_id)
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COLLATE=utf8mb4_0900_ai_ci COMMENT='公示条目';

CREATE TABLE objection (
  objection_id      BIGINT UNSIGNED NOT NULL AUTO_INCREMENT,
  batch_id          BIGINT UNSIGNED NOT NULL,
  publicity_item_id BIGINT UNSIGNED NULL,
  student_id        BIGINT UNSIGNED NOT NULL,
  content           VARCHAR(1000) NOT NULL,
  status            VARCHAR(16) NOT NULL DEFAULT 'PENDING' COMMENT 'PENDING/PROCESSING/RESOLVED/REJECTED',
  handled_by        BIGINT UNSIGNED NULL,
  handled_at        DATETIME(3) NULL,
  resolution        VARCHAR(1000) NULL,
  created_at        DATETIME(3) NOT NULL DEFAULT CURRENT_TIMESTAMP(3),
  updated_at        DATETIME(3) NOT NULL DEFAULT CURRENT_TIMESTAMP(3) ON UPDATE CURRENT_TIMESTAMP(3),
  PRIMARY KEY (objection_id),
  KEY idx_objection_batch_status (batch_id, status),
  KEY idx_objection_student (student_id)
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COLLATE=utf8mb4_0900_ai_ci COMMENT='公示异议';

CREATE TABLE notification (
  notice_id           BIGINT UNSIGNED NOT NULL AUTO_INCREMENT,
  receiver_account_id BIGINT UNSIGNED NOT NULL,
  notice_type         VARCHAR(32) NOT NULL COMMENT 'STAGE_OPEN/STAGE_CLOSE/INTERVIEW/INTERVIEW_REMIND/PROPOSAL/CONFIRM_REMIND/SUPPLEMENT/NEGOTIATION/PUBLICITY',
  title               VARCHAR(120) NOT NULL,
  content             VARCHAR(1000) NULL,
  biz_type            VARCHAR(32) NULL,
  biz_id              BIGINT UNSIGNED NULL,
  read_flag           TINYINT(1) NOT NULL DEFAULT 0,
  sent_at             DATETIME(3) NOT NULL DEFAULT CURRENT_TIMESTAMP(3),
  read_at             DATETIME(3) NULL,
  created_at          DATETIME(3) NOT NULL DEFAULT CURRENT_TIMESTAMP(3),
  PRIMARY KEY (notice_id),
  KEY idx_notice_receiver (receiver_account_id, read_flag, sent_at)
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COLLATE=utf8mb4_0900_ai_ci COMMENT='站内通知';

CREATE TABLE audit_log (
  log_id             BIGINT UNSIGNED NOT NULL AUTO_INCREMENT,
  operator_account_id BIGINT UNSIGNED NOT NULL,
  operator_role      VARCHAR(16) NULL,
  action_code        VARCHAR(48) NOT NULL,
  biz_type           VARCHAR(32) NOT NULL,
  biz_id             BIGINT UNSIGNED NULL,
  before_value       JSON NULL,
  after_value        JSON NULL,
  ip                 VARCHAR(45) NULL,
  user_agent         VARCHAR(255) NULL,
  created_at         DATETIME(3) NOT NULL DEFAULT CURRENT_TIMESTAMP(3),
  PRIMARY KEY (log_id),
  KEY idx_audit_biz (biz_type, biz_id, created_at),
  KEY idx_audit_operator (operator_account_id, created_at),
  KEY idx_audit_action (action_code, created_at)
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COLLATE=utf8mb4_0900_ai_ci COMMENT='审计日志（只插入）';

CREATE TABLE open_day_reservation (
  reservation_id BIGINT UNSIGNED NOT NULL AUTO_INCREMENT,
  student_id     BIGINT UNSIGNED NOT NULL,
  lab_id         BIGINT UNSIGNED NOT NULL,
  visit_date     DATE NOT NULL,
  time_slot      VARCHAR(16) NOT NULL,
  status         VARCHAR(16) NOT NULL DEFAULT 'RESERVED' COMMENT 'RESERVED/CHECKED_IN/CANCELED',
  remark         VARCHAR(255) NULL,
  created_at     DATETIME(3) NOT NULL DEFAULT CURRENT_TIMESTAMP(3),
  updated_at     DATETIME(3) NOT NULL DEFAULT CURRENT_TIMESTAMP(3) ON UPDATE CURRENT_TIMESTAMP(3),
  PRIMARY KEY (reservation_id),
  UNIQUE KEY uk_reservation_student_lab_slot (student_id, lab_id, visit_date, time_slot)
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COLLATE=utf8mb4_0900_ai_ci COMMENT='开放日预约（可选）';

SET FOREIGN_KEY_CHECKS = 1;
