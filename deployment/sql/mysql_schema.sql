-- Complete schema generated from current ORM; empty MySQL 8.0 database only.

-- No accounts, credentials, tasks, or historical user data are included.

-- The user's HeidiSQL export is retained separately for comparison.

SET NAMES utf8mb4;

CREATE TABLE accounts (
	id VARCHAR(64) NOT NULL, 
	username VARCHAR(50) NOT NULL, 
	password_hash VARCHAR(255) NOT NULL, 
	score FLOAT NOT NULL, 
	PRIMARY KEY (id)
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COLLATE=utf8mb4_unicode_ci;

CREATE INDEX ix_accounts_username ON accounts (username);

CREATE TABLE ai_agents (
	id VARCHAR(64) NOT NULL, 
	name VARCHAR(80) NOT NULL, 
	api_url VARCHAR(500), 
	type VARCHAR(30) NOT NULL, 
	score FLOAT NOT NULL, 
	enabled BOOL NOT NULL, 
	available BOOL NOT NULL, 
	last_checked_at DATETIME, 
	last_error TEXT, 
	metadata_json JSON NOT NULL, 
	provider VARCHAR(50), 
	model_name VARCHAR(255), 
	auth_type VARCHAR(30) NOT NULL DEFAULT 'none', 
	credential_ref VARCHAR(100), 
	generation_config_json JSON, 
	PRIMARY KEY (id)
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COLLATE=utf8mb4_unicode_ci;

CREATE TABLE heritage_sites (
	id VARCHAR(64) NOT NULL, 
	name VARCHAR(128) NOT NULL, 
	aliases JSON, 
	city VARCHAR(128), 
	province VARCHAR(128), 
	category VARCHAR(128), 
	dynasty VARCHAR(128), 
	built_year INTEGER, 
	description TEXT, 
	created_at DATETIME NOT NULL, 
	updated_at DATETIME NOT NULL, 
	PRIMARY KEY (id)
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COLLATE=utf8mb4_unicode_ci;

CREATE UNIQUE INDEX ix_heritage_sites_name ON heritage_sites (name);

CREATE TABLE rag_tasks (
	id VARCHAR(64) NOT NULL, 
	user_id VARCHAR(64) NOT NULL, 
	question TEXT NOT NULL, 
	answer_format TEXT NOT NULL, 
	ai_ids JSON NOT NULL, 
	required_worker_count INTEGER NOT NULL, 
	accepted_worker_ids JSON NOT NULL, 
	status VARCHAR(32) NOT NULL, 
	final_result TEXT, 
	metadata_json JSON NOT NULL, 
	mode VARCHAR(16) NOT NULL, 
	PRIMARY KEY (id), 
	FOREIGN KEY(user_id) REFERENCES accounts (id)
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COLLATE=utf8mb4_unicode_ci;

CREATE INDEX ix_rag_tasks_status ON rag_tasks (status);

CREATE INDEX ix_rag_tasks_user_id ON rag_tasks (user_id);

CREATE TABLE tasks (
	id VARCHAR(64) NOT NULL, 
	user_id VARCHAR(64) NOT NULL, 
	question TEXT NOT NULL, 
	answer_format TEXT NOT NULL, 
	ai_ids JSON NOT NULL, 
	required_worker_count INTEGER NOT NULL, 
	accepted_worker_ids JSON NOT NULL, 
	status VARCHAR(32) NOT NULL, 
	final_result TEXT, 
	metadata_json JSON NOT NULL, 
	mode VARCHAR(16) NOT NULL, 
	PRIMARY KEY (id), 
	FOREIGN KEY(user_id) REFERENCES accounts (id)
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COLLATE=utf8mb4_unicode_ci;

CREATE INDEX ix_tasks_status ON tasks (status);

CREATE INDEX ix_tasks_user_id ON tasks (user_id);

CREATE TABLE agent_executions (
	id VARCHAR(64) NOT NULL, 
	task_id VARCHAR(64) NOT NULL, 
	ai_id VARCHAR(64) NOT NULL, 
	context_id VARCHAR(64) NOT NULL, 
	status VARCHAR(32) NOT NULL DEFAULT 'waiting', 
	current_stage VARCHAR(32) NOT NULL DEFAULT 'running', 
	attempt_no INTEGER NOT NULL DEFAULT 1, 
	llm_request_count INTEGER NOT NULL DEFAULT 0, 
	started_at DATETIME, 
	completed_at DATETIME, 
	error_message TEXT, 
	created_at DATETIME NOT NULL DEFAULT CURRENT_TIMESTAMP, 
	PRIMARY KEY (id), 
	CONSTRAINT fk_agent_executions_task FOREIGN KEY(task_id) REFERENCES tasks (id), 
	CONSTRAINT uk_agent_execution_attempt UNIQUE (task_id, ai_id, attempt_no)
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COLLATE=utf8mb4_unicode_ci;

CREATE INDEX ix_agent_executions_ai_id ON agent_executions (ai_id);

CREATE INDEX ix_agent_executions_context_id ON agent_executions (context_id);

CREATE INDEX ix_agent_executions_status ON agent_executions (status);

CREATE INDEX ix_agent_executions_task_id ON agent_executions (task_id);

CREATE TABLE rag_task_agent_executions (
	id VARCHAR(64) NOT NULL, 
	task_id VARCHAR(64) NOT NULL, 
	ai_id VARCHAR(64) NOT NULL, 
	context_id VARCHAR(64) NOT NULL, 
	status VARCHAR(32) NOT NULL DEFAULT 'waiting', 
	current_stage VARCHAR(32) NOT NULL DEFAULT 'running', 
	attempt_no INTEGER NOT NULL DEFAULT 1, 
	llm_request_count INTEGER NOT NULL DEFAULT 0, 
	started_at DATETIME, 
	completed_at DATETIME, 
	error_message TEXT, 
	created_at DATETIME NOT NULL DEFAULT CURRENT_TIMESTAMP, 
	PRIMARY KEY (id), 
	CONSTRAINT rag_fk_agent_executions_task FOREIGN KEY(task_id) REFERENCES rag_tasks (id), 
	CONSTRAINT rag_uk_agent_execution_attempt UNIQUE (task_id, ai_id, attempt_no)
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COLLATE=utf8mb4_unicode_ci;

CREATE INDEX ix_rag_task_agent_executions_ai_id ON rag_task_agent_executions (ai_id);

CREATE INDEX ix_rag_task_agent_executions_context_id ON rag_task_agent_executions (context_id);

CREATE INDEX ix_rag_task_agent_executions_status ON rag_task_agent_executions (status);

CREATE INDEX ix_rag_task_agent_executions_task_id ON rag_task_agent_executions (task_id);

CREATE TABLE rag_task_contexts (
	id VARCHAR(64) NOT NULL, 
	task_id VARCHAR(64) NOT NULL, 
	version BIGINT NOT NULL DEFAULT 0, 
	created_at DATETIME NOT NULL DEFAULT CURRENT_TIMESTAMP, 
	updated_at DATETIME NOT NULL DEFAULT CURRENT_TIMESTAMP, 
	PRIMARY KEY (id), 
	CONSTRAINT rag_fk_task_contexts_task FOREIGN KEY(task_id) REFERENCES rag_tasks (id), 
	CONSTRAINT rag_uk_task_context_task_id UNIQUE (task_id)
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COLLATE=utf8mb4_unicode_ci;

CREATE TABLE rag_task_participants (
	id VARCHAR(64) NOT NULL, 
	task_id VARCHAR(64) NOT NULL, 
	worker_id VARCHAR(64) NOT NULL, 
	status VARCHAR(24) NOT NULL DEFAULT 'accepted', 
	accepted_at DATETIME NOT NULL DEFAULT CURRENT_TIMESTAMP, 
	submitted_at DATETIME, 
	PRIMARY KEY (id), 
	CONSTRAINT rag_fk_participant_task FOREIGN KEY(task_id) REFERENCES rag_tasks (id), 
	CONSTRAINT rag_fk_participant_worker FOREIGN KEY(worker_id) REFERENCES accounts (id), 
	CONSTRAINT rag_uk_participant_worker UNIQUE (task_id, worker_id)
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COLLATE=utf8mb4_unicode_ci;

CREATE INDEX rag_ix_participant_status ON rag_task_participants (task_id, status);

CREATE TABLE task_contexts (
	id VARCHAR(64) NOT NULL, 
	task_id VARCHAR(64) NOT NULL, 
	version BIGINT NOT NULL DEFAULT 0, 
	created_at DATETIME NOT NULL DEFAULT CURRENT_TIMESTAMP, 
	updated_at DATETIME NOT NULL DEFAULT CURRENT_TIMESTAMP, 
	PRIMARY KEY (id), 
	CONSTRAINT fk_task_contexts_task FOREIGN KEY(task_id) REFERENCES tasks (id), 
	CONSTRAINT uk_task_context_task_id UNIQUE (task_id)
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COLLATE=utf8mb4_unicode_ci;

CREATE TABLE task_knowledge_ingestions (
	id VARCHAR(64) NOT NULL, 
	task_id VARCHAR(64) NOT NULL, 
	status VARCHAR(20) NOT NULL DEFAULT 'pending', 
	document_id VARCHAR(128), 
	chunk_count INTEGER NOT NULL DEFAULT 0, 
	attempt_count INTEGER NOT NULL DEFAULT 0, 
	last_error TEXT, 
	metadata_json JSON NOT NULL, 
	created_at DATETIME NOT NULL DEFAULT CURRENT_TIMESTAMP, 
	updated_at DATETIME NOT NULL DEFAULT CURRENT_TIMESTAMP, 
	indexed_at DATETIME, 
	PRIMARY KEY (id), 
	CONSTRAINT fk_knowledge_ingestion_task FOREIGN KEY(task_id) REFERENCES tasks (id), 
	CONSTRAINT uk_knowledge_ingestion_task UNIQUE (task_id)
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COLLATE=utf8mb4_unicode_ci;

CREATE INDEX ix_knowledge_ingestion_status ON task_knowledge_ingestions (status);

CREATE TABLE answers (
	id VARCHAR(64) NOT NULL, 
	task_id VARCHAR(64) NOT NULL, 
	responder_id VARCHAR(64) NOT NULL, 
	source VARCHAR(20) NOT NULL, 
	content TEXT NOT NULL, 
	execution_id VARCHAR(64), 
	score_snapshot FLOAT NOT NULL, 
	weight FLOAT NOT NULL, 
	PRIMARY KEY (id), 
	FOREIGN KEY(execution_id) REFERENCES agent_executions (id), 
	FOREIGN KEY(task_id) REFERENCES tasks (id)
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COLLATE=utf8mb4_unicode_ci;

CREATE INDEX ix_answers_execution_id ON answers (execution_id);

CREATE INDEX ix_answers_responder_id ON answers (responder_id);

CREATE INDEX ix_answers_source ON answers (source);

CREATE INDEX ix_answers_task_id ON answers (task_id);

CREATE TABLE rag_task_answers (
	id VARCHAR(64) NOT NULL, 
	task_id VARCHAR(64) NOT NULL, 
	responder_id VARCHAR(64) NOT NULL, 
	source VARCHAR(20) NOT NULL, 
	content TEXT NOT NULL, 
	participant_id VARCHAR(64), 
	execution_id VARCHAR(64), 
	score_snapshot FLOAT NOT NULL, 
	weight FLOAT NOT NULL, 
	PRIMARY KEY (id), 
	FOREIGN KEY(execution_id) REFERENCES rag_task_agent_executions (id), 
	FOREIGN KEY(task_id) REFERENCES rag_tasks (id), 
	CONSTRAINT rag_fk_answer_participant FOREIGN KEY(participant_id) REFERENCES rag_task_participants (id), 
	CONSTRAINT rag_uk_answer_participant UNIQUE (participant_id)
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COLLATE=utf8mb4_unicode_ci;

CREATE INDEX ix_rag_task_answers_execution_id ON rag_task_answers (execution_id);

CREATE INDEX ix_rag_task_answers_responder_id ON rag_task_answers (responder_id);

CREATE INDEX ix_rag_task_answers_source ON rag_task_answers (source);

CREATE INDEX ix_rag_task_answers_task_id ON rag_task_answers (task_id);

CREATE TABLE rag_task_context_items (
	id VARCHAR(64) NOT NULL, 
	context_id VARCHAR(64) NOT NULL, 
	execution_id VARCHAR(64), 
	ai_id VARCHAR(64), 
	item_type VARCHAR(32) NOT NULL, 
	content LONGTEXT NOT NULL, 
	metadata_json JSON NOT NULL, 
	sequence_no INTEGER NOT NULL, 
	visibility VARCHAR(32) NOT NULL DEFAULT 'shared', 
	created_at DATETIME NOT NULL DEFAULT CURRENT_TIMESTAMP, 
	PRIMARY KEY (id), 
	CONSTRAINT rag_fk_context_items_context FOREIGN KEY(context_id) REFERENCES rag_task_contexts (id), 
	CONSTRAINT rag_uk_context_sequence UNIQUE (context_id, sequence_no)
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COLLATE=utf8mb4_unicode_ci;

CREATE INDEX rag_ix_context_items_ai_id ON rag_task_context_items (ai_id);

CREATE INDEX rag_ix_context_items_context_id ON rag_task_context_items (context_id);

CREATE INDEX rag_ix_context_items_execution_id ON rag_task_context_items (execution_id);

CREATE INDEX rag_ix_context_items_type ON rag_task_context_items (item_type);

CREATE TABLE rag_task_processing_jobs (
	id VARCHAR(64) NOT NULL, 
	task_id VARCHAR(64) NOT NULL, 
	phase VARCHAR(24) NOT NULL, 
	status VARCHAR(24) NOT NULL DEFAULT 'pending', 
	attempts INTEGER NOT NULL DEFAULT 0, 
	retry_limit INTEGER NOT NULL, 
	next_run_at DATETIME NOT NULL DEFAULT CURRENT_TIMESTAMP, 
	lease_owner VARCHAR(64), 
	lease_expires_at DATETIME, 
	generation INTEGER NOT NULL DEFAULT 0, 
	external_started BOOL NOT NULL DEFAULT 0, 
	execution_id VARCHAR(64), 
	payload_json JSON NOT NULL, 
	prepared_text LONGTEXT, 
	last_error TEXT, 
	created_at DATETIME NOT NULL DEFAULT CURRENT_TIMESTAMP, 
	updated_at DATETIME NOT NULL DEFAULT CURRENT_TIMESTAMP, 
	PRIMARY KEY (id), 
	CONSTRAINT rag_fk_job_execution FOREIGN KEY(execution_id) REFERENCES rag_task_agent_executions (id), 
	CONSTRAINT rag_fk_job_task FOREIGN KEY(task_id) REFERENCES rag_tasks (id), 
	CONSTRAINT rag_uk_job_phase UNIQUE (task_id, phase)
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COLLATE=utf8mb4_unicode_ci;

CREATE INDEX rag_ix_job_poll ON rag_task_processing_jobs (status, next_run_at, lease_expires_at);

CREATE TABLE rag_task_results (
	id VARCHAR(64) NOT NULL, 
	task_id VARCHAR(64) NOT NULL, 
	aggregator_ai_id VARCHAR(64) NOT NULL, 
	execution_id VARCHAR(64) NOT NULL, 
	content LONGTEXT NOT NULL, 
	answer_count INTEGER NOT NULL, 
	version INTEGER NOT NULL DEFAULT 1, 
	metadata_json JSON NOT NULL, 
	created_at DATETIME NOT NULL DEFAULT CURRENT_TIMESTAMP, 
	PRIMARY KEY (id), 
	CONSTRAINT rag_fk_result_execution FOREIGN KEY(execution_id) REFERENCES rag_task_agent_executions (id), 
	CONSTRAINT rag_fk_result_task FOREIGN KEY(task_id) REFERENCES rag_tasks (id), 
	CONSTRAINT rag_uk_result_task UNIQUE (task_id)
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COLLATE=utf8mb4_unicode_ci;

CREATE TABLE task_context_items (
	id VARCHAR(64) NOT NULL, 
	context_id VARCHAR(64) NOT NULL, 
	execution_id VARCHAR(64), 
	ai_id VARCHAR(64), 
	item_type VARCHAR(32) NOT NULL, 
	content LONGTEXT NOT NULL, 
	metadata_json JSON NOT NULL, 
	sequence_no INTEGER NOT NULL, 
	visibility VARCHAR(32) NOT NULL DEFAULT 'shared', 
	created_at DATETIME NOT NULL DEFAULT CURRENT_TIMESTAMP, 
	PRIMARY KEY (id), 
	CONSTRAINT fk_context_items_context FOREIGN KEY(context_id) REFERENCES task_contexts (id), 
	CONSTRAINT uk_context_sequence UNIQUE (context_id, sequence_no)
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COLLATE=utf8mb4_unicode_ci;

CREATE INDEX ix_context_items_ai_id ON task_context_items (ai_id);

CREATE INDEX ix_context_items_context_id ON task_context_items (context_id);

CREATE INDEX ix_context_items_execution_id ON task_context_items (execution_id);

CREATE INDEX ix_context_items_type ON task_context_items (item_type);

CREATE TABLE crowd_task_participants (
	id VARCHAR(64) NOT NULL, 
	task_id VARCHAR(64) NOT NULL, 
	worker_id VARCHAR(64) NOT NULL, 
	answer_id VARCHAR(64), 
	status VARCHAR(20) NOT NULL DEFAULT 'accepted', 
	created_at DATETIME NOT NULL DEFAULT CURRENT_TIMESTAMP, 
	submitted_at DATETIME, 
	updated_at DATETIME NOT NULL DEFAULT CURRENT_TIMESTAMP, 
	PRIMARY KEY (id), 
	CONSTRAINT fk_crowd_participant_answer FOREIGN KEY(answer_id) REFERENCES answers (id), 
	CONSTRAINT fk_crowd_participant_task FOREIGN KEY(task_id) REFERENCES tasks (id), 
	CONSTRAINT fk_crowd_participant_worker FOREIGN KEY(worker_id) REFERENCES accounts (id), 
	CONSTRAINT uk_crowd_participant_answer UNIQUE (answer_id), 
	CONSTRAINT uk_crowd_task_worker UNIQUE (task_id, worker_id)
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COLLATE=utf8mb4_unicode_ci;

CREATE INDEX ix_crowd_participant_task_status ON crowd_task_participants (task_id, status);

CREATE TABLE rag_task_knowledge_ingestions (
	id VARCHAR(64) NOT NULL, 
	task_id VARCHAR(64) NOT NULL, 
	result_id VARCHAR(64) NOT NULL, 
	status VARCHAR(24) NOT NULL DEFAULT 'pending', 
	source_id VARCHAR(512) NOT NULL, 
	document_id VARCHAR(128), 
	chunk_count INTEGER, 
	attempt_count INTEGER NOT NULL DEFAULT 0, 
	last_error TEXT, 
	plan_json JSON, 
	created_at DATETIME NOT NULL DEFAULT CURRENT_TIMESTAMP, 
	updated_at DATETIME NOT NULL DEFAULT CURRENT_TIMESTAMP, 
	PRIMARY KEY (id), 
	CONSTRAINT rag_fk_ingestion_result FOREIGN KEY(result_id) REFERENCES rag_task_results (id), 
	CONSTRAINT rag_fk_ingestion_task FOREIGN KEY(task_id) REFERENCES rag_tasks (id), 
	CONSTRAINT rag_uk_ingestion_result UNIQUE (result_id), 
	CONSTRAINT rag_uk_ingestion_task UNIQUE (task_id)
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COLLATE=utf8mb4_unicode_ci;
