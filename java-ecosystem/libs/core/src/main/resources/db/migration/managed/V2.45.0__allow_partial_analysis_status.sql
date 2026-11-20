-- Upgraded databases retain the older Hibernate-generated enum constraint.
ALTER TABLE code_analysis DROP CONSTRAINT IF EXISTS code_analysis_status_check;
ALTER TABLE code_analysis ADD CONSTRAINT code_analysis_status_check
    CHECK (status IN ('ACCEPTED', 'PARTIAL', 'REJECTED', 'PENDING', 'ERROR'));
