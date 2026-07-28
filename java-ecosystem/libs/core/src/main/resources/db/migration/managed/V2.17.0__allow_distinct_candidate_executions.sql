-- Final review findings are execution-bound evidence. A changed policy/input
-- identity at the same PR head must persist a distinct candidate output while
-- exact retries remain idempotent through uq_code_analysis_execution_id.
ALTER TABLE code_analysis
    DROP CONSTRAINT IF EXISTS uq_code_analysis_project_commit;
