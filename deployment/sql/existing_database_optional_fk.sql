-- OPTIONAL existing-database repair. NOT part of fresh initialization.
-- Review a backup and database name first. Never run against an unknown database.
-- Verified local difference: answers.execution_id has an index but no foreign key.
-- Step 1: run these read-only queries. Proceed only if orphan_count = 0 and
-- no existing foreign key covers answers.execution_id. Do not delete orphan rows.
SELECT DATABASE() AS selected_database;
SELECT COUNT(*) AS orphan_count
FROM answers AS a LEFT JOIN agent_executions AS e ON a.execution_id = e.id
WHERE a.execution_id IS NOT NULL AND e.id IS NULL;
SELECT CONSTRAINT_NAME FROM information_schema.KEY_COLUMN_USAGE
WHERE TABLE_SCHEMA = DATABASE() AND TABLE_NAME = 'answers'
  AND COLUMN_NAME = 'execution_id' AND REFERENCED_TABLE_NAME IS NOT NULL;
-- Step 2: operator runs this statement ONLY after inspecting Step 1.
-- ALTER TABLE answers ADD CONSTRAINT fk_answers_execution
--   FOREIGN KEY (execution_id) REFERENCES agent_executions (id);
-- heritage_sites.name already has UNIQUE uk_heritage_sites_name. It supplies
-- the lookup index; do not add a redundant index merely to match its ORM name.
