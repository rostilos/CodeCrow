package org.rostilos.codecrow.core.model.job;

public enum JobType {
    PR_ANALYSIS,
    BRANCH_ANALYSIS,
    BRANCH_RECONCILIATION,
    RAG_INITIAL_INDEX,
    RAG_INCREMENTAL_INDEX,
    MANUAL_ANALYSIS,
    REPO_SYNC,
    // Comment command job types
    SUMMARIZE_COMMAND,
    ASK_COMMAND,
    ANALYZE_COMMAND,
    REVIEW_COMMAND,
    // Ignored comment events (not CodeCrow commands)
    IGNORED_COMMENT
}
