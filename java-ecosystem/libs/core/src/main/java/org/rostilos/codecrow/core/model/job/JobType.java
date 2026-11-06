package org.rostilos.codecrow.core.model.job;

public enum JobType {
    PR_ANALYSIS,
    BRANCH_ANALYSIS,
    BRANCH_RECONCILIATION,
    REPOSITORY_INDEX_BUILD,
    MANUAL_ANALYSIS,
    REPO_SYNC,
    // Comment command job types
    SUMMARIZE_COMMAND,
    ASK_COMMAND,
    ANALYZE_COMMAND,
    REVIEW_COMMAND,
    QA_DOC_COMMAND,
    // Ignored comment events (not CodeCrow commands)
    IGNORED_COMMENT
}
