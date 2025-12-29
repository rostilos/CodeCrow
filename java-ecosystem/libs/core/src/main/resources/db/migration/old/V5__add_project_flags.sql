-- Add project-level flags for PR auto-review and commenting
ALTER TABLE project
    ADD COLUMN auto_review_prs boolean NOT NULL DEFAULT false,
    ADD COLUMN comment_on_prs boolean NOT NULL DEFAULT false;
