-- Add default_branch_id to project table to support branch-specific stats
-- This allows each project to have a designated default branch for primary stats display

ALTER TABLE project 
    ADD COLUMN default_branch_id BIGINT;

-- Add foreign key constraint to branch table
ALTER TABLE project 
    ADD CONSTRAINT fk_project_default_branch 
    FOREIGN KEY (default_branch_id) 
    REFERENCES branch(id) 
    ON DELETE SET NULL;

-- Add index for performance
CREATE INDEX idx_project_default_branch ON project(default_branch_id);

-- Add comment for documentation
COMMENT ON COLUMN project.default_branch_id IS 'The default branch for this project (typically main/master). Stats shown on project list use this branch.';
