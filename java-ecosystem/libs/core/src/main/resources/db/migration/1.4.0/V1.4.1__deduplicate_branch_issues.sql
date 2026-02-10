-- V1.4.1: Remove duplicate branch_issue rows that accumulated because the
-- deduplication key was based on code_analysis_issue_id (database PK) rather
-- than on the issue's semantic content.  Each PR analysis creates fresh
-- CodeAnalysisIssue rows with new IDs, so the same logical issue
-- (same file, line, severity, category) ended up with N BranchIssue rows.
--
-- Strategy: for each (branch_id, file_path, line_number, severity, category)
-- group keep only the row with the LOWEST id (oldest / first-detected) and
-- delete the rest.  Afterwards recompute the denormalized counters on branch.

-- 1. Delete duplicate branch_issues, keeping the first (lowest id) per group
DELETE FROM branch_issue
WHERE id NOT IN (
    SELECT keeper_id FROM (
        SELECT MIN(bi.id) AS keeper_id
        FROM branch_issue bi
        JOIN code_analysis_issue cai ON bi.code_analysis_issue_id = cai.id
        GROUP BY bi.branch_id,
                 cai.file_path,
                 cai.line_number,
                 cai.severity,
                 COALESCE(cai.issue_category, '__NONE__')
    ) AS keepers
);

-- 2. Recompute denormalized branch issue counts
UPDATE branch b SET
    total_issues = COALESCE(sub.total_unresolved, 0),
    high_severity_count = COALESCE(sub.high_count, 0),
    medium_severity_count = COALESCE(sub.medium_count, 0),
    low_severity_count = COALESCE(sub.low_count, 0),
    info_severity_count = COALESCE(sub.info_count, 0),
    resolved_count = COALESCE(sub.resolved_total, 0),
    updated_at = NOW()
FROM (
    SELECT
        bi.branch_id,
        COUNT(*) FILTER (WHERE bi.is_resolved = false) AS total_unresolved,
        COUNT(*) FILTER (WHERE bi.is_resolved = false AND cai.severity = 'HIGH')   AS high_count,
        COUNT(*) FILTER (WHERE bi.is_resolved = false AND cai.severity = 'MEDIUM') AS medium_count,
        COUNT(*) FILTER (WHERE bi.is_resolved = false AND cai.severity = 'LOW')    AS low_count,
        COUNT(*) FILTER (WHERE bi.is_resolved = false AND cai.severity = 'INFO')   AS info_count,
        COUNT(*) FILTER (WHERE bi.is_resolved = true)                              AS resolved_total
    FROM branch_issue bi
    JOIN code_analysis_issue cai ON bi.code_analysis_issue_id = cai.id
    GROUP BY bi.branch_id
) sub
WHERE b.id = sub.branch_id;

-- Also zero-out branches that lost all issues after cleanup
UPDATE branch SET
    total_issues = 0,
    high_severity_count = 0,
    medium_severity_count = 0,
    low_severity_count = 0,
    info_severity_count = 0,
    resolved_count = 0,
    updated_at = NOW()
WHERE id NOT IN (SELECT DISTINCT branch_id FROM branch_issue);
