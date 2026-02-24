package org.rostilos.codecrow.core.util.tracking;

/**
 * Represents an entity that can participate in issue tracking across analyses.
 * Both {@code CodeAnalysisIssue} (raw/new) and {@code BranchIssue} (base/existing)
 * implement this interface so the {@link IssueTracker} can operate generically.
 * <p>
 * Inspired by SonarQube's {@code Trackable} interface, but simplified for
 * AI-sourced issues where the "rule" concept maps to {@code issueFingerprint}.
 */
public interface Trackable {

    /**
     * Stable issue identity: SHA-256 of (category + lineHash + normalizedTitle).
     * Unlike line numbers, this survives code shifts because it includes content-based
     * anchoring via the lineHash component.
     *
     * @return the fingerprint string, or {@code null} for legacy issues without hashes
     */
    String getIssueFingerprint();

    /**
     * The 1-based line number where the issue was detected (or currently lives).
     *
     * @return line number, or {@code null} for file-level issues
     */
    Integer getLine();

    /**
     * MD5 hash of the whitespace-normalized source line content at {@link #getLine()}.
     * This is the primary mechanism for surviving insertions/deletions elsewhere in the file —
     * when lines shift, the hash stays the same as long as the source content doesn't change.
     *
     * @return 32-char hex MD5 hash, or {@code null} if line content was unavailable
     */
    String getLineHash();

    /**
     * Repository-relative file path where the issue exists.
     *
     * @return file path, never {@code null}
     */
    String getFilePath();
}
