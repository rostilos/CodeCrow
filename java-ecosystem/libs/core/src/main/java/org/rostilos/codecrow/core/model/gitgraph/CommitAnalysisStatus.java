package org.rostilos.codecrow.core.model.gitgraph;

/**
 * Tracks whether a commit has been covered by an analysis pass.
 * <p>
 * NOT_ANALYZED — default; this commit's changes have not been reviewed by the analysis engine.
 * ANALYZED    — this commit was covered by a successful analysis (branch or PR).
 * FAILED      — the analysis that covered this commit failed; eligible for retry.
 */
public enum CommitAnalysisStatus {
    NOT_ANALYZED,
    ANALYZED,
    FAILED
}
