-- Existing findings use proposed-file coordinates. Deleted-code findings retain
-- their base revision coordinates through persistence and provider publication.
ALTER TABLE code_analysis_issue ADD COLUMN anchor_side VARCHAR(8) NOT NULL DEFAULT 'HEAD';
ALTER TABLE branch_issue ADD COLUMN anchor_side VARCHAR(8) NOT NULL DEFAULT 'HEAD';
