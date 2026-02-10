package org.rostilos.codecrow.analysisengine.util;

import java.nio.charset.StandardCharsets;
import java.security.MessageDigest;
import java.security.NoSuchAlgorithmException;
import java.util.ArrayList;
import java.util.Collections;
import java.util.List;

/**
 * Computes a content-based fingerprint of a unified diff.
 * <p>
 * Only actual change lines ({@code +} / {@code -}) are included — context lines,
 * hunk headers ({@code @@}), and file headers ({@code +++} / {@code ---} / {@code diff --git})
 * are excluded. The change lines are sorted to make the fingerprint stable regardless
 * of file ordering within the diff.
 * <p>
 * This allows detecting that two PRs carry the same code changes even if they target
 * different branches (different merge-base → different context/hunk headers).
 */
public final class DiffFingerprintUtil {

    private DiffFingerprintUtil() { /* utility */ }

    /**
     * Compute a SHA-256 hex digest of the normalised change lines in the given diff.
     *
     * @param rawDiff the filtered unified diff (may be {@code null} or empty)
     * @return 64-char lowercase hex string, or {@code null} if the diff is blank
     */
    public static String compute(String rawDiff) {
        if (rawDiff == null || rawDiff.isBlank()) {
            return null;
        }

        List<String> changeLines = extractChangeLines(rawDiff);
        if (changeLines.isEmpty()) {
            return null;
        }

        // Sort for stability across different file orderings
        Collections.sort(changeLines);

        try {
            MessageDigest digest = MessageDigest.getInstance("SHA-256");
            for (String line : changeLines) {
                digest.update(line.getBytes(StandardCharsets.UTF_8));
                digest.update((byte) '\n');
            }
            return bytesToHex(digest.digest());
        } catch (NoSuchAlgorithmException e) {
            // SHA-256 is guaranteed by the JVM spec — should never happen
            throw new IllegalStateException("SHA-256 not available", e);
        }
    }

    /**
     * Extract only the actual change lines from a unified diff.
     * A "change line" starts with exactly one {@code +} or {@code -} and is NOT
     * a file header ({@code +++}, {@code ---}) or a diff metadata line.
     */
    private static List<String> extractChangeLines(String diff) {
        List<String> lines = new ArrayList<>();
        // Normalise line endings
        String normalised = diff.replace("\r\n", "\n").replace("\r", "\n");
        for (String raw : normalised.split("\n")) {
            String line = trimTrailingWhitespace(raw);
            if (line.isEmpty()) {
                continue;
            }
            char first = line.charAt(0);
            if (first != '+' && first != '-') {
                continue;
            }
            // Skip file-level headers: "+++", "---", "diff --git"
            if (line.startsWith("+++") || line.startsWith("---")) {
                continue;
            }
            if (line.startsWith("diff ")) {
                continue;
            }
            lines.add(line);
        }
        return lines;
    }

    private static String trimTrailingWhitespace(String s) {
        int end = s.length();
        while (end > 0 && Character.isWhitespace(s.charAt(end - 1))) {
            end--;
        }
        return s.substring(0, end);
    }

    private static String bytesToHex(byte[] bytes) {
        StringBuilder sb = new StringBuilder(bytes.length * 2);
        for (byte b : bytes) {
            sb.append(String.format("%02x", b & 0xff));
        }
        return sb.toString();
    }
}
