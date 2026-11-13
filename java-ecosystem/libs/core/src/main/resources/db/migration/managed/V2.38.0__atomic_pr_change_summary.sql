ALTER TABLE code_analysis ADD COLUMN pr_change_summary_payload TEXT;

UPDATE code_analysis analysis
SET pr_change_summary_payload = summary.payload
FROM pr_change_summary summary
WHERE summary.analysis_id = analysis.id;

DROP TABLE pr_change_summary;
