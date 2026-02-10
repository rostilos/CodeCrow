package org.rostilos.codecrow.core.model.branch;

/**
 * Represents the health status of a branch's analysis state.
 * A branch is HEALTHY when its lastSuccessfulCommitHash matches the latest analyzed commit.
 * A branch becomes STALE when an analysis fails, meaning the stored state is behind reality.
 *
 * <p>Used by the delta-based branch analysis to decide whether a branch needs reconciliation
 * and to track consecutive failures for backoff purposes.</p>
 *
 * <p><b>TODO: Implement BranchHealthScheduler in pipeline-agent</b> — a {@code @Scheduled} component that:
 * <ol>
 *   <li>Queries all projects with {@code branchAnalysisEnabled=true}</li>
 *   <li>For each project, lists VCS branches matching {@code branchPushPatterns} via
 *       {@code BranchPatternMatcher.shouldAnalyzeBranch()}</li>
 *   <li>For STALE/UNKNOWN branches, compares {@code lastSuccessfulCommitHash} with VCS HEAD
 *       via {@code VcsClientActions.getLatestCommitHash()}</li>
 *   <li>Submits delta analysis via {@code webhookExecutor} for branches that are behind</li>
 *   <li>Applies exponential backoff based on {@code consecutiveFailures}</li>
 *   <li>Limits batch size per cycle to avoid VCS API rate limits and thread pool saturation</li>
 * </ol>
 * </p>
 */
public enum BranchHealthStatus {
    /** Branch analysis completed successfully — lastSuccessfulCommitHash is up to date */
    HEALTHY,
    /** Branch analysis failed — lastSuccessfulCommitHash is behind the actual branch HEAD */
    STALE,
    /** Branch is currently being re-analyzed by the health scheduler */
    HEALING,
    /** Initial state — branch has never completed a health-tracked analysis (e.g., legacy data) */
    UNKNOWN
}
