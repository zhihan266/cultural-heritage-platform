-- Manual migration record only; the application does not load this file.
-- The live ai_agents table has already been altered by its owner.
-- DO NOT rerun this SQL against a table that already contains these columns.
-- Use only when manually upgrading an older schema, after reviewing a backup.

ALTER TABLE `ai_agents`
    ADD COLUMN `provider` VARCHAR(50) NULL,
    ADD COLUMN `model_name` VARCHAR(255) NULL,
    ADD COLUMN `auth_type` VARCHAR(30) NOT NULL DEFAULT 'none',
    ADD COLUMN `credential_ref` VARCHAR(100) NULL,
    ADD COLUMN `generation_config_json` JSON NULL;

-- Backfill missing legacy values without overwriting configured providers/auth types.
UPDATE `ai_agents` SET `provider` = 'local' WHERE `provider` IS NULL;
UPDATE `ai_agents` SET `auth_type` = 'none'
    WHERE `auth_type` IS NULL OR `auth_type` = '';
