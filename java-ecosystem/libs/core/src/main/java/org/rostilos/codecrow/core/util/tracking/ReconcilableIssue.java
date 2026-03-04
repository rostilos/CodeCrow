package org.rostilos.codecrow.core.util.tracking;

import org.rostilos.codecrow.core.model.codeanalysis.IssueCategory;
import org.rostilos.codecrow.core.model.codeanalysis.IssueScope;

/**
 * Unified interface for issue entities that participate in reconciliation.
 * <p>
 * Both {@link org.rostilos.codecrow.core.model.codeanalysis.CodeAnalysisIssue} (PR-level)
 * and {@link org.rostilos.codecrow.core.model.branch.BranchIssue} (branch-level) implement
 * this interface, enabling the shared {@code IssueReconciliationEngine} to operate on
 * either entity type without knowing the persistence model.
 * <p>
 * Extends {@link Trackable} so that both entities can be directly passed to
 * {@link IssueTracker} without wrapper records.
 *
 * <h3>Contract for {@link #getLine()}</h3>
 * Must return the <b>current best-known line position</b>:
 * <ul>
 *   <li>For {@code CodeAnalysisIssue}: returns {@code lineNumber} (immutable after ingestion)</li>
 *   <li>For {@code BranchIssue}: returns {@code currentLineNumber} if set, else {@code lineNumber}</li>
 * </ul>
 */
public interface ReconcilableIssue extends Trackable {

    /** Database primary key. */
    Long getId();

    /** The issue category (SECURITY, PERFORMANCE, etc.). */
    IssueCategory getIssueCategory();

    /** Short issue title. */
    String getTitle();

    /** Detailed description / reason. */
    String getReason();

    /**
     * The scope of this issue: LINE, BLOCK, FUNCTION, or FILE.
     *
     * @return the scope, or {@code null} for legacy issues (treated as LINE)
     */
    IssueScope getIssueScope();

    /** The original detection line number (immutable). */
    Integer getLineNumber();

    /**
     * End line number for BLOCK/FUNCTION scoped issues.
     *
     * @return end line (inclusive), or {@code null} for LINE/FILE scoped issues
     */
    Integer getEndLineNumber();

    /**
     * Start line of the enclosing AST scope (function, class, block).
     * Set by the AST parser after snippet anchoring.
     *
     * @return 1-based start line of the scope, or {@code null} if AST parsing was not performed
     */
    Integer getScopeStartLine();

    /** Verbatim source line the LLM referenced when reporting this issue. */
    String getCodeSnippet();

    /** MD5 of the context window around the source line (range derived from endLine / snippet). */
    String getLineHashContext();

    /**
     * Category-agnostic fingerprint: SHA-256 of (lineHash + normalizedTitle).
     * Used as secondary dedup key when primary fingerprint diverges due to category drift.
     */
    String getContentFingerprint();

    /** Whether this issue has been resolved. */
    boolean isResolved();

    /**
     * Effective scope, defaulting to LINE for legacy issues where scope is null.
     */
    default IssueScope getEffectiveScope() {
        IssueScope scope = getIssueScope();
        return scope != null ? scope : IssueScope.LINE;
    }

    /**
     * Whether this issue is "anchored" to a specific line in the source code.
     * <p>
     * FILE-scoped issues and issues at line ≤ 1 with no code snippet are considered unanchored.
     * Unanchored issues cannot be tracked deterministically and must fall through to
     * fingerprint matching or AI reconciliation.
     */
    default boolean isAnchored() {
        if (getEffectiveScope() == IssueScope.FILE) {
            return false;
        }
        Integer line = getLine();
        String snippet = getCodeSnippet();
        return line != null && line > 1 || (snippet != null && !snippet.isBlank());
    }
}
