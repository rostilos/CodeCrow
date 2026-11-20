package org.rostilos.codecrow.core.model.branch;

/**
 * Represents the health status of a branch's analysis state.
 * A branch is HEALTHY when its lastSuccessfulCommitHash matches the latest analyzed commit.
 * A branch becomes STALE when an analysis fails, meaning the stored state is behind reality.
 *
 * Health is diagnostic state, not authorization to repeat paid analysis.
 * Failed analysis or reconciliation requires a fresh accepted event or explicit retry.
 */
public enum BranchHealthStatus {
    /** Branch analysis completed successfully — lastSuccessfulCommitHash is up to date */
    HEALTHY,
    /** Branch analysis failed — lastSuccessfulCommitHash is behind the actual branch HEAD */
    STALE,
    /** Branch is currently being re-analyzed */
    HEALING,
    /** Initial state — branch has never completed a health-tracked analysis (e.g., legacy data) */
    UNKNOWN
}
