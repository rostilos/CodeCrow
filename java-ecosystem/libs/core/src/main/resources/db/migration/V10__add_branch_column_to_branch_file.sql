-- Add branch_name to branch_file and scope uniqueness per branch
ALTER TABLE branch_file ADD COLUMN branch_name VARCHAR(200);

UPDATE branch_file SET branch_name = 'unknown';

ALTER TABLE branch_file ALTER COLUMN branch_name SET NOT NULL;

ALTER TABLE branch_file DROP CONSTRAINT IF EXISTS uq_branch_file_project_path;
ALTER TABLE branch_file ADD CONSTRAINT uq_branch_file_project_branch_path UNIQUE (project_id, branch_name, file_path);

