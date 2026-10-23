-- One-time MySQL migration for the unified Account model.
-- The live schema was inspected before generating this file:
--   * ix_accounts_role is the only index containing accounts.role
--   * accounts.role has no foreign-key constraint
--   * tasks references accounts.id, not accounts.role

ALTER TABLE `accounts`
    DROP INDEX `ix_accounts_role`,
    DROP COLUMN `role`;
