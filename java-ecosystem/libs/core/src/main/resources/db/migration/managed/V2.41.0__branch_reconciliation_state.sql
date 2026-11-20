ALTER TABLE branch_issue ADD COLUMN row_version bigint NOT NULL DEFAULT 0;
ALTER TABLE branch ADD COLUMN row_version bigint NOT NULL DEFAULT 0;
ALTER TABLE branch ADD COLUMN reconciliation_pending boolean NOT NULL DEFAULT false;
ALTER TABLE branch ADD COLUMN reconciliation_diagnostic text;
ALTER TABLE branch ADD COLUMN reconciliation_attempts integer NOT NULL DEFAULT 0;
ALTER TABLE branch ADD COLUMN reconciliation_attempted_at timestamptz;
ALTER TABLE branch ADD COLUMN retry_commit_hash varchar(40);
CREATE INDEX idx_branch_pending_reconciliation ON branch (reconciliation_attempted_at)
    WHERE reconciliation_pending;
