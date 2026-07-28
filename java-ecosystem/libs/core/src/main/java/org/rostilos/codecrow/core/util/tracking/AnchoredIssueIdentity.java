package org.rostilos.codecrow.core.util.tracking;

import java.nio.charset.StandardCharsets;
import java.security.MessageDigest;
import java.security.NoSuchAlgorithmException;

/**
 * Builds exact, deterministic identities for findings backed by source evidence.
 * <p>
 * A model-generated fingerprint is not sufficient by itself: fingerprints created
 * without source content use a {@code no_hash} placeholder and can collide across
 * unrelated findings. This utility therefore requires either an actual line hash or
 * an exact code snippet before an identity is mergeable.
 */
public final class AnchoredIssueIdentity {

    private AnchoredIssueIdentity() {
        // Utility class.
    }

    /**
     * Bind an existing finding fingerprint to its concrete source anchor.
     *
     * @return a deterministic identity, or {@code null} when no reliable anchor exists
     */
    public static String forFingerprint(
            ReconcilableIssue issue,
            String fingerprint
    ) {
        if (issue == null || fingerprint == null || fingerprint.isBlank()) {
            return null;
        }

        String lineHash = issue.getLineHash();
        if (lineHash != null && !lineHash.isBlank()) {
            return fingerprint + ":line-hash:" + lineHash;
        }

        String snippet = issue.getCodeSnippet();
        if (snippet == null || snippet.isBlank()) {
            return null;
        }

        String normalizedSnippet = normalizeLineEndings(snippet);
        String scope = issue.getIssueScope() != null
                ? issue.getIssueScope().name()
                : "UNKNOWN";
        Integer line = issue.getLineNumber();
        return fingerprint + ":snippet:" + scope + ":"
                + (line != null ? line : 0) + ":" + normalizedSnippet;
    }

    /**
     * Compute the value persisted in {@code BranchIssue.contentFingerprint}.
     * <p>
     * The branch table has a branch-wide unique index on that column. Hashing the
     * full repository-relative path into the stored identity prevents identical
     * source lines and titles in different files from colliding. Recomputing the
     * category-agnostic fingerprint from source fields also makes this method work
     * for both historical rows and rows already carrying the branch storage value.
     *
     * @return a 64-character SHA-256 identity, or {@code null} for unanchored issues
     */
    public static String forBranchStorage(ReconcilableIssue issue) {
        if (issue == null) {
            return null;
        }
        String path = normalizeRepositoryPath(issue.getFilePath());
        if (path == null) {
            return null;
        }

        String contentFingerprint = IssueFingerprint.computeContentFingerprint(
                issue.getLineHash(),
                issue.getTitle()
        );
        String anchored = forFingerprint(issue, contentFingerprint);
        if (anchored == null) {
            return null;
        }
        return sha256Hex(path + "\0" + anchored);
    }

    static String normalizeRepositoryPath(String value) {
        if (value == null || value.isBlank()) {
            return null;
        }
        String normalized = value.trim().replace('\\', '/');
        while (normalized.startsWith("./")) {
            normalized = normalized.substring(2);
        }
        while (normalized.startsWith("/")) {
            normalized = normalized.substring(1);
        }
        return normalized.isBlank() ? null : normalized;
    }

    private static String normalizeLineEndings(String value) {
        return value.replace("\r\n", "\n").replace('\r', '\n');
    }

    private static String sha256Hex(String value) {
        try {
            MessageDigest digest = MessageDigest.getInstance("SHA-256");
            byte[] hash = digest.digest(value.getBytes(StandardCharsets.UTF_8));
            StringBuilder result = new StringBuilder(64);
            for (byte item : hash) {
                result.append(String.format("%02x", item & 0xff));
            }
            return result.toString();
        } catch (NoSuchAlgorithmException exception) {
            throw new IllegalStateException("SHA-256 not available", exception);
        }
    }
}
