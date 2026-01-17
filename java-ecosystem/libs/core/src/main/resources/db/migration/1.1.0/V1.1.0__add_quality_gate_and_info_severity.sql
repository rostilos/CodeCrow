-- Quality Gate Feature Migration
-- Adds INFO severity support, analysis result status, and quality gate entities

-- 1. Add INFO severity count to code_analysis table
ALTER TABLE code_analysis ADD COLUMN IF NOT EXISTS info_severity_count INTEGER NOT NULL DEFAULT 0;

-- 2. Add analysis result status to code_analysis table
ALTER TABLE code_analysis ADD COLUMN IF NOT EXISTS analysis_result VARCHAR(20);

-- 3. Add INFO severity count to branch table
ALTER TABLE branch ADD COLUMN IF NOT EXISTS info_severity_count INTEGER NOT NULL DEFAULT 0;

-- 4. Create quality_gate table
CREATE TABLE IF NOT EXISTS quality_gate (
    id BIGSERIAL PRIMARY KEY,
    workspace_id BIGINT NOT NULL REFERENCES workspace(id) ON DELETE CASCADE,
    name VARCHAR(100) NOT NULL,
    description VARCHAR(500),
    is_default BOOLEAN NOT NULL DEFAULT FALSE,
    is_active BOOLEAN NOT NULL DEFAULT TRUE,
    created_at TIMESTAMP WITH TIME ZONE NOT NULL DEFAULT NOW(),
    updated_at TIMESTAMP WITH TIME ZONE NOT NULL DEFAULT NOW(),
    CONSTRAINT uq_quality_gate_workspace_name UNIQUE (workspace_id, name)
);

-- 5. Create quality_gate_condition table
CREATE TABLE IF NOT EXISTS quality_gate_condition (
    id BIGSERIAL PRIMARY KEY,
    quality_gate_id BIGINT NOT NULL REFERENCES quality_gate(id) ON DELETE CASCADE,
    metric VARCHAR(50) NOT NULL,
    severity VARCHAR(20),
    comparator VARCHAR(10) NOT NULL,
    threshold_value INTEGER NOT NULL,
    is_enabled BOOLEAN NOT NULL DEFAULT TRUE
);

-- 6. Add quality gate reference to project table
ALTER TABLE project ADD COLUMN IF NOT EXISTS quality_gate_id BIGINT REFERENCES quality_gate(id) ON DELETE SET NULL;

-- 7. Create indexes
CREATE INDEX IF NOT EXISTS idx_quality_gate_workspace ON quality_gate(workspace_id);
CREATE INDEX IF NOT EXISTS idx_quality_gate_condition_gate ON quality_gate_condition(quality_gate_id);
CREATE INDEX IF NOT EXISTS idx_project_quality_gate ON project(quality_gate_id);

-- 8. Update existing LOW issues that might be INFO-level
-- This is a one-time migration to reclassify certain LOW issues as INFO
-- You can customize this based on your criteria
-- Example: issues containing "ensure", "consider", "might", etc. could be INFO

-- Uncomment and customize if you want to auto-migrate certain LOW issues to INFO:
-- UPDATE code_analysis_issue 
-- SET severity = 'INFO' 
-- WHERE severity = 'LOW' 
-- AND (
--     LOWER(reason) LIKE '%consider%' 
--     OR LOWER(reason) LIKE '%ensure%'
--     OR LOWER(reason) LIKE '%might%'
--     OR LOWER(reason) LIKE '%could%'
--     OR LOWER(reason) LIKE '%suggestion%'
--     OR LOWER(reason) LIKE '%recommendation%'
-- );

-- After migration, update the counts
-- UPDATE code_analysis ca SET 
--     info_severity_count = (SELECT COUNT(*) FROM code_analysis_issue WHERE analysis_id = ca.id AND severity = 'INFO'),
--     low_severity_count = (SELECT COUNT(*) FROM code_analysis_issue WHERE analysis_id = ca.id AND severity = 'LOW')
-- WHERE EXISTS (SELECT 1 FROM code_analysis_issue WHERE analysis_id = ca.id);

-- UPDATE branch b SET 
--     info_severity_count = (SELECT COUNT(*) FROM branch_issue bi JOIN code_analysis_issue cai ON bi.code_issue_id = cai.id WHERE bi.branch_id = b.id AND cai.severity = 'INFO' AND NOT bi.is_resolved),
--     low_severity_count = (SELECT COUNT(*) FROM branch_issue bi JOIN code_analysis_issue cai ON bi.code_issue_id = cai.id WHERE bi.branch_id = b.id AND cai.severity = 'LOW' AND NOT bi.is_resolved)
-- WHERE EXISTS (SELECT 1 FROM branch_issue WHERE branch_id = b.id);
