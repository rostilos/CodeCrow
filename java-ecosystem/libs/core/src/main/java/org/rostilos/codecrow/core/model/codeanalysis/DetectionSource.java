package org.rostilos.codecrow.core.model.codeanalysis;

/**
 * Indicates how an issue was originally detected.
 * <ul>
 *   <li>{@link #PR_ANALYSIS} — found during a pull request review</li>
 *   <li>{@link #DIRECT_PUSH_ANALYSIS} — found during hybrid branch analysis
 *       triggered by a direct push (no PR)</li>
 * </ul>
 * Stored on both {@code CodeAnalysisIssue} and {@code BranchIssue} for display
 * on the issue details page.
 */
public enum DetectionSource {
    PR_ANALYSIS,
    DIRECT_PUSH_ANALYSIS
}
