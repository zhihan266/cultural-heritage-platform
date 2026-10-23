-- --------------------------------------------------------
-- 主机:                           127.0.0.1
-- 服务器版本:                        8.0.41 - MySQL Community Server - GPL
-- 服务器操作系统:                      Win64
-- HeidiSQL 版本:                  12.15.0.7171
-- --------------------------------------------------------

/*!40101 SET @OLD_CHARACTER_SET_CLIENT=@@CHARACTER_SET_CLIENT */;
/*!40101 SET NAMES utf8 */;
/*!50503 SET NAMES utf8mb4 */;
/*!40103 SET @OLD_TIME_ZONE=@@TIME_ZONE */;
/*!40103 SET TIME_ZONE='+00:00' */;
/*!40014 SET @OLD_FOREIGN_KEY_CHECKS=@@FOREIGN_KEY_CHECKS, FOREIGN_KEY_CHECKS=0 */;
/*!40101 SET @OLD_SQL_MODE=@@SQL_MODE, SQL_MODE='NO_AUTO_VALUE_ON_ZERO' */;
/*!40111 SET @OLD_SQL_NOTES=@@SQL_NOTES, SQL_NOTES=0 */;


-- 导出 aijiu_dev 的数据库结构
CREATE DATABASE IF NOT EXISTS `aijiu_dev` /*!40100 DEFAULT CHARACTER SET utf8mb4 COLLATE utf8mb4_unicode_ci */ /*!80016 DEFAULT ENCRYPTION='N' */;
USE `aijiu_dev`;

-- 导出  表 aijiu_dev.accounts 结构
CREATE TABLE IF NOT EXISTS `accounts` (
  `id` varchar(64) COLLATE utf8mb4_unicode_ci NOT NULL,
  `username` varchar(50) COLLATE utf8mb4_unicode_ci NOT NULL,
  `password_hash` varchar(255) COLLATE utf8mb4_unicode_ci NOT NULL,
  `score` float NOT NULL,
  PRIMARY KEY (`id`),
  KEY `ix_accounts_username` (`username`)
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COLLATE=utf8mb4_unicode_ci;

-- 数据导出被取消选择。

-- 导出  表 aijiu_dev.agent_executions 结构
CREATE TABLE IF NOT EXISTS `agent_executions` (
  `id` varchar(64) CHARACTER SET utf8mb4 COLLATE utf8mb4_unicode_ci NOT NULL,
  `task_id` varchar(64) CHARACTER SET utf8mb4 COLLATE utf8mb4_unicode_ci NOT NULL,
  `ai_id` varchar(64) CHARACTER SET utf8mb4 COLLATE utf8mb4_unicode_ci NOT NULL,
  `context_id` varchar(64) CHARACTER SET utf8mb4 COLLATE utf8mb4_unicode_ci NOT NULL,
  `status` varchar(32) CHARACTER SET utf8mb4 COLLATE utf8mb4_unicode_ci NOT NULL DEFAULT 'waiting',
  `current_stage` varchar(32) CHARACTER SET utf8mb4 COLLATE utf8mb4_unicode_ci NOT NULL DEFAULT 'running',
  `attempt_no` int NOT NULL DEFAULT '1',
  `llm_request_count` int NOT NULL DEFAULT '0',
  `started_at` datetime DEFAULT NULL,
  `completed_at` datetime DEFAULT NULL,
  `error_message` text CHARACTER SET utf8mb4 COLLATE utf8mb4_unicode_ci,
  `created_at` datetime NOT NULL DEFAULT CURRENT_TIMESTAMP,
  PRIMARY KEY (`id`),
  UNIQUE KEY `uk_agent_execution_attempt` (`task_id`,`ai_id`,`attempt_no`),
  KEY `ix_agent_executions_task_id` (`task_id`),
  KEY `ix_agent_executions_ai_id` (`ai_id`),
  KEY `ix_agent_executions_context_id` (`context_id`),
  KEY `ix_agent_executions_status` (`status`),
  CONSTRAINT `fk_agent_executions_task` FOREIGN KEY (`task_id`) REFERENCES `tasks` (`id`)
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COLLATE=utf8mb4_unicode_ci;

-- 数据导出被取消选择。

-- 导出  表 aijiu_dev.ai_agents 结构
CREATE TABLE IF NOT EXISTS `ai_agents` (
  `id` varchar(64) COLLATE utf8mb4_unicode_ci NOT NULL,
  `name` varchar(80) COLLATE utf8mb4_unicode_ci NOT NULL,
  `api_url` varchar(500) COLLATE utf8mb4_unicode_ci DEFAULT NULL,
  `type` varchar(30) COLLATE utf8mb4_unicode_ci NOT NULL,
  `score` float NOT NULL,
  `enabled` tinyint(1) NOT NULL,
  `available` tinyint(1) NOT NULL,
  `last_checked_at` datetime DEFAULT NULL,
  `last_error` text COLLATE utf8mb4_unicode_ci,
  `metadata_json` json NOT NULL,
  `provider` varchar(50) COLLATE utf8mb4_unicode_ci DEFAULT NULL COMMENT '模型提供方，例如 local、deepseek',
  `model_name` varchar(255) COLLATE utf8mb4_unicode_ci DEFAULT NULL COMMENT '请求模型服务时使用的模型名称',
  `auth_type` varchar(30) COLLATE utf8mb4_unicode_ci NOT NULL DEFAULT 'none' COMMENT '认证方式：none、bearer',
  `credential_ref` varchar(100) COLLATE utf8mb4_unicode_ci DEFAULT NULL COMMENT '凭证环境变量名，例如 DEEPSEEK_API_KEY',
  `generation_config_json` json DEFAULT NULL COMMENT '模型生成参数，例如 temperature、max_tokens、thinking',
  PRIMARY KEY (`id`)
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COLLATE=utf8mb4_unicode_ci;

-- 数据导出被取消选择。

-- 导出  表 aijiu_dev.answers 结构
CREATE TABLE IF NOT EXISTS `answers` (
  `id` varchar(64) COLLATE utf8mb4_unicode_ci NOT NULL,
  `task_id` varchar(64) COLLATE utf8mb4_unicode_ci NOT NULL,
  `responder_id` varchar(64) COLLATE utf8mb4_unicode_ci NOT NULL,
  `execution_id` varchar(64) COLLATE utf8mb4_unicode_ci DEFAULT NULL,
  `source` varchar(20) COLLATE utf8mb4_unicode_ci NOT NULL,
  `content` text COLLATE utf8mb4_unicode_ci NOT NULL,
  `score_snapshot` float NOT NULL,
  `weight` float NOT NULL,
  PRIMARY KEY (`id`),
  KEY `ix_answers_responder_id` (`responder_id`),
  KEY `ix_answers_task_id` (`task_id`),
  KEY `ix_answers_source` (`source`),
  KEY `ix_answers_execution_id` (`execution_id`),
  CONSTRAINT `answers_ibfk_1` FOREIGN KEY (`task_id`) REFERENCES `tasks` (`id`)
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COLLATE=utf8mb4_unicode_ci;

-- 数据导出被取消选择。

-- 导出  表 aijiu_dev.crowd_task_participants 结构
CREATE TABLE IF NOT EXISTS `crowd_task_participants` (
  `id` varchar(64) COLLATE utf8mb4_unicode_ci NOT NULL,
  `task_id` varchar(64) COLLATE utf8mb4_unicode_ci NOT NULL,
  `worker_id` varchar(64) COLLATE utf8mb4_unicode_ci NOT NULL,
  `answer_id` varchar(64) COLLATE utf8mb4_unicode_ci DEFAULT NULL,
  `status` varchar(20) COLLATE utf8mb4_unicode_ci NOT NULL DEFAULT 'accepted',
  `created_at` datetime NOT NULL DEFAULT CURRENT_TIMESTAMP,
  `submitted_at` datetime DEFAULT NULL,
  `updated_at` datetime NOT NULL DEFAULT CURRENT_TIMESTAMP ON UPDATE CURRENT_TIMESTAMP,
  PRIMARY KEY (`id`),
  UNIQUE KEY `uk_crowd_task_worker` (`task_id`,`worker_id`),
  UNIQUE KEY `uk_crowd_participant_answer` (`answer_id`),
  KEY `ix_crowd_participant_task_status` (`task_id`,`status`),
  KEY `fk_crowd_participant_worker` (`worker_id`),
  CONSTRAINT `fk_crowd_participant_answer` FOREIGN KEY (`answer_id`) REFERENCES `answers` (`id`),
  CONSTRAINT `fk_crowd_participant_task` FOREIGN KEY (`task_id`) REFERENCES `tasks` (`id`),
  CONSTRAINT `fk_crowd_participant_worker` FOREIGN KEY (`worker_id`) REFERENCES `accounts` (`id`)
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COLLATE=utf8mb4_unicode_ci;

-- 数据导出被取消选择。

-- 导出  表 aijiu_dev.heritage_sites 结构
CREATE TABLE IF NOT EXISTS `heritage_sites` (
  `id` varchar(64) COLLATE utf8mb4_unicode_ci NOT NULL,
  `name` varchar(128) COLLATE utf8mb4_unicode_ci NOT NULL,
  `aliases` json DEFAULT NULL,
  `city` varchar(128) COLLATE utf8mb4_unicode_ci DEFAULT NULL,
  `province` varchar(128) COLLATE utf8mb4_unicode_ci DEFAULT NULL,
  `category` varchar(128) COLLATE utf8mb4_unicode_ci DEFAULT NULL,
  `dynasty` varchar(128) COLLATE utf8mb4_unicode_ci DEFAULT NULL,
  `built_year` int DEFAULT NULL,
  `description` text COLLATE utf8mb4_unicode_ci,
  `created_at` datetime NOT NULL DEFAULT CURRENT_TIMESTAMP,
  `updated_at` datetime NOT NULL DEFAULT CURRENT_TIMESTAMP ON UPDATE CURRENT_TIMESTAMP,
  PRIMARY KEY (`id`),
  UNIQUE KEY `uk_heritage_sites_name` (`name`)
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COLLATE=utf8mb4_unicode_ci;

-- 数据导出被取消选择。

-- 导出  表 aijiu_dev.rag_task_agent_executions 结构
CREATE TABLE IF NOT EXISTS `rag_task_agent_executions` (
  `id` varchar(64) COLLATE utf8mb4_unicode_ci NOT NULL,
  `task_id` varchar(64) COLLATE utf8mb4_unicode_ci NOT NULL,
  `ai_id` varchar(64) COLLATE utf8mb4_unicode_ci NOT NULL,
  `context_id` varchar(64) COLLATE utf8mb4_unicode_ci NOT NULL,
  `status` varchar(32) COLLATE utf8mb4_unicode_ci NOT NULL DEFAULT 'waiting',
  `current_stage` varchar(32) COLLATE utf8mb4_unicode_ci NOT NULL DEFAULT 'running',
  `attempt_no` int NOT NULL DEFAULT '1',
  `llm_request_count` int NOT NULL DEFAULT '0',
  `started_at` datetime DEFAULT NULL,
  `completed_at` datetime DEFAULT NULL,
  `error_message` text COLLATE utf8mb4_unicode_ci,
  `created_at` datetime NOT NULL DEFAULT CURRENT_TIMESTAMP,
  PRIMARY KEY (`id`),
  UNIQUE KEY `rag_uk_agent_execution_attempt` (`task_id`,`ai_id`,`attempt_no`),
  KEY `ix_rag_task_agent_executions_ai_id` (`ai_id`),
  KEY `ix_rag_task_agent_executions_context_id` (`context_id`),
  KEY `ix_rag_task_agent_executions_status` (`status`),
  KEY `ix_rag_task_agent_executions_task_id` (`task_id`),
  CONSTRAINT `rag_fk_agent_executions_task` FOREIGN KEY (`task_id`) REFERENCES `rag_tasks` (`id`)
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COLLATE=utf8mb4_unicode_ci;

-- 数据导出被取消选择。

-- 导出  表 aijiu_dev.rag_task_answers 结构
CREATE TABLE IF NOT EXISTS `rag_task_answers` (
  `id` varchar(64) COLLATE utf8mb4_unicode_ci NOT NULL,
  `task_id` varchar(64) COLLATE utf8mb4_unicode_ci NOT NULL,
  `responder_id` varchar(64) COLLATE utf8mb4_unicode_ci NOT NULL,
  `source` varchar(20) COLLATE utf8mb4_unicode_ci NOT NULL,
  `content` text COLLATE utf8mb4_unicode_ci NOT NULL,
  `execution_id` varchar(64) COLLATE utf8mb4_unicode_ci DEFAULT NULL,
  `score_snapshot` float NOT NULL,
  `weight` float NOT NULL,
  `participant_id` varchar(64) COLLATE utf8mb4_unicode_ci DEFAULT NULL,
  PRIMARY KEY (`id`),
  UNIQUE KEY `rag_uk_answer_participant` (`participant_id`),
  KEY `ix_rag_task_answers_execution_id` (`execution_id`),
  KEY `ix_rag_task_answers_responder_id` (`responder_id`),
  KEY `ix_rag_task_answers_source` (`source`),
  KEY `ix_rag_task_answers_task_id` (`task_id`),
  CONSTRAINT `rag_fk_answer_participant` FOREIGN KEY (`participant_id`) REFERENCES `rag_task_participants` (`id`),
  CONSTRAINT `rag_task_answers_ibfk_1` FOREIGN KEY (`task_id`) REFERENCES `rag_tasks` (`id`),
  CONSTRAINT `rag_task_answers_ibfk_2` FOREIGN KEY (`execution_id`) REFERENCES `rag_task_agent_executions` (`id`)
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COLLATE=utf8mb4_unicode_ci;

-- 数据导出被取消选择。

-- 导出  表 aijiu_dev.rag_task_context_items 结构
CREATE TABLE IF NOT EXISTS `rag_task_context_items` (
  `id` varchar(64) COLLATE utf8mb4_unicode_ci NOT NULL,
  `context_id` varchar(64) COLLATE utf8mb4_unicode_ci NOT NULL,
  `execution_id` varchar(64) COLLATE utf8mb4_unicode_ci DEFAULT NULL,
  `ai_id` varchar(64) COLLATE utf8mb4_unicode_ci DEFAULT NULL,
  `item_type` varchar(32) COLLATE utf8mb4_unicode_ci NOT NULL,
  `content` longtext COLLATE utf8mb4_unicode_ci NOT NULL,
  `metadata_json` json NOT NULL,
  `sequence_no` int NOT NULL,
  `visibility` varchar(32) COLLATE utf8mb4_unicode_ci NOT NULL DEFAULT 'shared',
  `created_at` datetime NOT NULL DEFAULT CURRENT_TIMESTAMP,
  PRIMARY KEY (`id`),
  UNIQUE KEY `rag_uk_context_sequence` (`context_id`,`sequence_no`),
  KEY `rag_ix_context_items_ai_id` (`ai_id`),
  KEY `rag_ix_context_items_context_id` (`context_id`),
  KEY `rag_ix_context_items_execution_id` (`execution_id`),
  KEY `rag_ix_context_items_type` (`item_type`),
  CONSTRAINT `rag_fk_context_items_context` FOREIGN KEY (`context_id`) REFERENCES `rag_task_contexts` (`id`)
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COLLATE=utf8mb4_unicode_ci;

-- 数据导出被取消选择。

-- 导出  表 aijiu_dev.rag_task_contexts 结构
CREATE TABLE IF NOT EXISTS `rag_task_contexts` (
  `id` varchar(64) COLLATE utf8mb4_unicode_ci NOT NULL,
  `task_id` varchar(64) COLLATE utf8mb4_unicode_ci NOT NULL,
  `version` bigint NOT NULL DEFAULT '0',
  `created_at` datetime NOT NULL DEFAULT CURRENT_TIMESTAMP,
  `updated_at` datetime NOT NULL DEFAULT CURRENT_TIMESTAMP,
  PRIMARY KEY (`id`),
  UNIQUE KEY `rag_uk_task_context_task_id` (`task_id`),
  CONSTRAINT `rag_fk_task_contexts_task` FOREIGN KEY (`task_id`) REFERENCES `rag_tasks` (`id`)
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COLLATE=utf8mb4_unicode_ci;

-- 数据导出被取消选择。

-- 导出  表 aijiu_dev.rag_task_knowledge_ingestions 结构
CREATE TABLE IF NOT EXISTS `rag_task_knowledge_ingestions` (
  `id` varchar(64) COLLATE utf8mb4_unicode_ci NOT NULL,
  `task_id` varchar(64) COLLATE utf8mb4_unicode_ci NOT NULL,
  `result_id` varchar(64) COLLATE utf8mb4_unicode_ci NOT NULL,
  `status` varchar(24) COLLATE utf8mb4_unicode_ci NOT NULL DEFAULT 'pending',
  `source_id` varchar(512) COLLATE utf8mb4_unicode_ci NOT NULL,
  `document_id` varchar(128) COLLATE utf8mb4_unicode_ci DEFAULT NULL,
  `chunk_count` int DEFAULT NULL,
  `attempt_count` int NOT NULL DEFAULT '0',
  `last_error` text COLLATE utf8mb4_unicode_ci,
  `plan_json` json DEFAULT NULL,
  `created_at` datetime NOT NULL DEFAULT CURRENT_TIMESTAMP,
  `updated_at` datetime NOT NULL DEFAULT CURRENT_TIMESTAMP,
  PRIMARY KEY (`id`),
  UNIQUE KEY `rag_uk_ingestion_task` (`task_id`),
  UNIQUE KEY `rag_uk_ingestion_result` (`result_id`),
  CONSTRAINT `rag_fk_ingestion_result` FOREIGN KEY (`result_id`) REFERENCES `rag_task_results` (`id`),
  CONSTRAINT `rag_fk_ingestion_task` FOREIGN KEY (`task_id`) REFERENCES `rag_tasks` (`id`)
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COLLATE=utf8mb4_unicode_ci;

-- 数据导出被取消选择。

-- 导出  表 aijiu_dev.rag_task_participants 结构
CREATE TABLE IF NOT EXISTS `rag_task_participants` (
  `id` varchar(64) COLLATE utf8mb4_unicode_ci NOT NULL,
  `task_id` varchar(64) COLLATE utf8mb4_unicode_ci NOT NULL,
  `worker_id` varchar(64) COLLATE utf8mb4_unicode_ci NOT NULL,
  `status` varchar(24) COLLATE utf8mb4_unicode_ci NOT NULL DEFAULT 'accepted',
  `accepted_at` datetime NOT NULL DEFAULT CURRENT_TIMESTAMP,
  `submitted_at` datetime DEFAULT NULL,
  PRIMARY KEY (`id`),
  UNIQUE KEY `rag_uk_participant_worker` (`task_id`,`worker_id`),
  KEY `rag_fk_participant_worker` (`worker_id`),
  KEY `rag_ix_participant_status` (`task_id`,`status`),
  CONSTRAINT `rag_fk_participant_task` FOREIGN KEY (`task_id`) REFERENCES `rag_tasks` (`id`),
  CONSTRAINT `rag_fk_participant_worker` FOREIGN KEY (`worker_id`) REFERENCES `accounts` (`id`)
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COLLATE=utf8mb4_unicode_ci;

-- 数据导出被取消选择。

-- 导出  表 aijiu_dev.rag_task_processing_jobs 结构
CREATE TABLE IF NOT EXISTS `rag_task_processing_jobs` (
  `id` varchar(64) COLLATE utf8mb4_unicode_ci NOT NULL,
  `task_id` varchar(64) COLLATE utf8mb4_unicode_ci NOT NULL,
  `phase` varchar(24) COLLATE utf8mb4_unicode_ci NOT NULL,
  `status` varchar(24) COLLATE utf8mb4_unicode_ci NOT NULL DEFAULT 'pending',
  `attempts` int NOT NULL DEFAULT '0',
  `retry_limit` int NOT NULL,
  `next_run_at` datetime NOT NULL DEFAULT CURRENT_TIMESTAMP,
  `lease_owner` varchar(64) COLLATE utf8mb4_unicode_ci DEFAULT NULL,
  `lease_expires_at` datetime DEFAULT NULL,
  `generation` int NOT NULL DEFAULT '0',
  `external_started` tinyint(1) NOT NULL DEFAULT '0',
  `execution_id` varchar(64) COLLATE utf8mb4_unicode_ci DEFAULT NULL,
  `payload_json` json NOT NULL,
  `prepared_text` longtext COLLATE utf8mb4_unicode_ci,
  `last_error` text COLLATE utf8mb4_unicode_ci,
  `created_at` datetime NOT NULL DEFAULT CURRENT_TIMESTAMP,
  `updated_at` datetime NOT NULL DEFAULT CURRENT_TIMESTAMP,
  PRIMARY KEY (`id`),
  UNIQUE KEY `rag_uk_job_phase` (`task_id`,`phase`),
  KEY `rag_fk_job_execution` (`execution_id`),
  KEY `rag_ix_job_poll` (`status`,`next_run_at`,`lease_expires_at`),
  CONSTRAINT `rag_fk_job_execution` FOREIGN KEY (`execution_id`) REFERENCES `rag_task_agent_executions` (`id`),
  CONSTRAINT `rag_fk_job_task` FOREIGN KEY (`task_id`) REFERENCES `rag_tasks` (`id`)
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COLLATE=utf8mb4_unicode_ci;

-- 数据导出被取消选择。

-- 导出  表 aijiu_dev.rag_task_results 结构
CREATE TABLE IF NOT EXISTS `rag_task_results` (
  `id` varchar(64) COLLATE utf8mb4_unicode_ci NOT NULL,
  `task_id` varchar(64) COLLATE utf8mb4_unicode_ci NOT NULL,
  `aggregator_ai_id` varchar(64) COLLATE utf8mb4_unicode_ci NOT NULL,
  `execution_id` varchar(64) COLLATE utf8mb4_unicode_ci NOT NULL,
  `content` longtext COLLATE utf8mb4_unicode_ci NOT NULL,
  `answer_count` int NOT NULL,
  `version` int NOT NULL DEFAULT '1',
  `metadata_json` json NOT NULL,
  `created_at` datetime NOT NULL DEFAULT CURRENT_TIMESTAMP,
  PRIMARY KEY (`id`),
  UNIQUE KEY `rag_uk_result_task` (`task_id`),
  KEY `rag_fk_result_execution` (`execution_id`),
  CONSTRAINT `rag_fk_result_execution` FOREIGN KEY (`execution_id`) REFERENCES `rag_task_agent_executions` (`id`),
  CONSTRAINT `rag_fk_result_task` FOREIGN KEY (`task_id`) REFERENCES `rag_tasks` (`id`)
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COLLATE=utf8mb4_unicode_ci;

-- 数据导出被取消选择。

-- 导出  表 aijiu_dev.rag_tasks 结构
CREATE TABLE IF NOT EXISTS `rag_tasks` (
  `id` varchar(64) COLLATE utf8mb4_unicode_ci NOT NULL,
  `user_id` varchar(64) COLLATE utf8mb4_unicode_ci NOT NULL,
  `question` text COLLATE utf8mb4_unicode_ci NOT NULL,
  `answer_format` text COLLATE utf8mb4_unicode_ci NOT NULL,
  `ai_ids` json NOT NULL,
  `required_worker_count` int NOT NULL,
  `accepted_worker_ids` json NOT NULL,
  `status` varchar(32) COLLATE utf8mb4_unicode_ci NOT NULL,
  `final_result` text COLLATE utf8mb4_unicode_ci,
  `metadata_json` json NOT NULL,
  `mode` varchar(16) COLLATE utf8mb4_unicode_ci NOT NULL,
  PRIMARY KEY (`id`),
  KEY `ix_rag_tasks_status` (`status`),
  KEY `ix_rag_tasks_user_id` (`user_id`),
  CONSTRAINT `rag_tasks_ibfk_1` FOREIGN KEY (`user_id`) REFERENCES `accounts` (`id`)
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COLLATE=utf8mb4_unicode_ci;

-- 数据导出被取消选择。

-- 导出  表 aijiu_dev.task_context_items 结构
CREATE TABLE IF NOT EXISTS `task_context_items` (
  `id` varchar(64) CHARACTER SET utf8mb4 COLLATE utf8mb4_unicode_ci NOT NULL,
  `context_id` varchar(64) CHARACTER SET utf8mb4 COLLATE utf8mb4_unicode_ci NOT NULL,
  `execution_id` varchar(64) CHARACTER SET utf8mb4 COLLATE utf8mb4_unicode_ci DEFAULT NULL,
  `ai_id` varchar(64) CHARACTER SET utf8mb4 COLLATE utf8mb4_unicode_ci DEFAULT NULL,
  `item_type` varchar(32) CHARACTER SET utf8mb4 COLLATE utf8mb4_unicode_ci NOT NULL,
  `content` longtext CHARACTER SET utf8mb4 COLLATE utf8mb4_unicode_ci NOT NULL,
  `metadata_json` json NOT NULL,
  `sequence_no` int NOT NULL,
  `visibility` varchar(32) CHARACTER SET utf8mb4 COLLATE utf8mb4_unicode_ci NOT NULL DEFAULT 'shared',
  `created_at` datetime NOT NULL DEFAULT CURRENT_TIMESTAMP,
  PRIMARY KEY (`id`),
  UNIQUE KEY `uk_context_sequence` (`context_id`,`sequence_no`),
  KEY `ix_context_items_context_id` (`context_id`),
  KEY `ix_context_items_execution_id` (`execution_id`),
  KEY `ix_context_items_ai_id` (`ai_id`),
  KEY `ix_context_items_type` (`item_type`),
  CONSTRAINT `fk_context_items_context` FOREIGN KEY (`context_id`) REFERENCES `task_contexts` (`id`)
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COLLATE=utf8mb4_unicode_ci;

-- 数据导出被取消选择。

-- 导出  表 aijiu_dev.task_contexts 结构
CREATE TABLE IF NOT EXISTS `task_contexts` (
  `id` varchar(64) CHARACTER SET utf8mb4 COLLATE utf8mb4_unicode_ci NOT NULL,
  `task_id` varchar(64) CHARACTER SET utf8mb4 COLLATE utf8mb4_unicode_ci NOT NULL,
  `version` bigint NOT NULL DEFAULT '0',
  `created_at` datetime NOT NULL DEFAULT CURRENT_TIMESTAMP,
  `updated_at` datetime NOT NULL DEFAULT CURRENT_TIMESTAMP ON UPDATE CURRENT_TIMESTAMP,
  PRIMARY KEY (`id`),
  UNIQUE KEY `uk_task_context_task_id` (`task_id`),
  CONSTRAINT `fk_task_contexts_task` FOREIGN KEY (`task_id`) REFERENCES `tasks` (`id`)
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COLLATE=utf8mb4_unicode_ci;

-- 数据导出被取消选择。

-- 导出  表 aijiu_dev.task_knowledge_ingestions 结构
CREATE TABLE IF NOT EXISTS `task_knowledge_ingestions` (
  `id` varchar(64) COLLATE utf8mb4_unicode_ci NOT NULL,
  `task_id` varchar(64) COLLATE utf8mb4_unicode_ci NOT NULL,
  `status` varchar(20) COLLATE utf8mb4_unicode_ci NOT NULL DEFAULT 'pending',
  `document_id` varchar(128) COLLATE utf8mb4_unicode_ci DEFAULT NULL,
  `chunk_count` int NOT NULL DEFAULT '0',
  `attempt_count` int NOT NULL DEFAULT '0',
  `last_error` text COLLATE utf8mb4_unicode_ci,
  `metadata_json` json NOT NULL,
  `created_at` datetime NOT NULL DEFAULT CURRENT_TIMESTAMP,
  `updated_at` datetime NOT NULL DEFAULT CURRENT_TIMESTAMP ON UPDATE CURRENT_TIMESTAMP,
  `indexed_at` datetime DEFAULT NULL,
  PRIMARY KEY (`id`),
  UNIQUE KEY `uk_knowledge_ingestion_task` (`task_id`),
  KEY `ix_knowledge_ingestion_status` (`status`),
  CONSTRAINT `fk_knowledge_ingestion_task` FOREIGN KEY (`task_id`) REFERENCES `tasks` (`id`)
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COLLATE=utf8mb4_unicode_ci;

-- 数据导出被取消选择。

-- 导出  表 aijiu_dev.tasks 结构
CREATE TABLE IF NOT EXISTS `tasks` (
  `id` varchar(64) COLLATE utf8mb4_unicode_ci NOT NULL,
  `user_id` varchar(64) COLLATE utf8mb4_unicode_ci NOT NULL,
  `question` text COLLATE utf8mb4_unicode_ci NOT NULL,
  `answer_format` text COLLATE utf8mb4_unicode_ci NOT NULL,
  `ai_ids` json NOT NULL,
  `required_worker_count` int NOT NULL,
  `accepted_worker_ids` json NOT NULL,
  `status` varchar(32) COLLATE utf8mb4_unicode_ci NOT NULL,
  `final_result` text COLLATE utf8mb4_unicode_ci,
  `metadata_json` json NOT NULL,
  `mode` varchar(16) COLLATE utf8mb4_unicode_ci NOT NULL DEFAULT 'normal',
  PRIMARY KEY (`id`),
  KEY `ix_tasks_user_id` (`user_id`),
  KEY `ix_tasks_status` (`status`),
  CONSTRAINT `tasks_ibfk_1` FOREIGN KEY (`user_id`) REFERENCES `accounts` (`id`)
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COLLATE=utf8mb4_unicode_ci;

-- 数据导出被取消选择。

/*!40103 SET TIME_ZONE=IFNULL(@OLD_TIME_ZONE, 'system') */;
/*!40101 SET SQL_MODE=IFNULL(@OLD_SQL_MODE, '') */;
/*!40014 SET FOREIGN_KEY_CHECKS=IFNULL(@OLD_FOREIGN_KEY_CHECKS, 1) */;
/*!40101 SET CHARACTER_SET_CLIENT=@OLD_CHARACTER_SET_CLIENT */;
/*!40111 SET SQL_NOTES=IFNULL(@OLD_SQL_NOTES, 1) */;
