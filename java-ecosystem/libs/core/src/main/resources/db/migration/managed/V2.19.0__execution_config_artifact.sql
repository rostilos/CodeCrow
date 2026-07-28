ALTER TABLE review_artifact
    DROP CONSTRAINT ck_review_artifact_kind;

ALTER TABLE review_artifact
    ADD CONSTRAINT ck_review_artifact_kind
    CHECK (kind IN (
        'raw-diff',
        'source-file',
        'pr-enrichment',
        'execution-config',
        'review-output'
    ));
