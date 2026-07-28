package org.rostilos.codecrow.analysisengine.util;

import java.nio.charset.StandardCharsets;
import java.security.MessageDigest;
import java.security.NoSuchAlgorithmException;
import java.util.ArrayList;
import java.util.Collections;
import java.util.List;
import java.util.Map;
import java.util.TreeMap;

/**
 * Computes a canonical identity for an exact unified diff snapshot.
 * File sections may arrive in a different order, but file paths, hunk locations,
 * context, and changed-line order remain part of the identity.
 */
public final class DiffFingerprintUtil {

    private DiffFingerprintUtil() { /* utility */ }

    /**
     * Compute a SHA-256 hex digest of the canonical exact diff.
     *
     * @param rawDiff the filtered unified diff (may be {@code null} or empty)
     * @return 64-char lowercase hex string, or {@code null} if the diff is blank
     */
    public static String compute(String rawDiff) {
        if (rawDiff == null || rawDiff.isBlank()) {
            return null;
        }

        return compute(rawDiff, Map.of());
    }

    /**
     * Include review-affecting host inputs in the identity without including secrets.
     */
    public static String compute(String rawDiff, Map<String, String> reviewInputs) {
        if (rawDiff == null || rawDiff.isBlank()) return null;
        List<String> sections = canonicalSections(rawDiff);
        if (sections.isEmpty()) return null;

        try {
            MessageDigest digest = MessageDigest.getInstance("SHA-256");
            updateField(digest, "diff-sections", Integer.toString(sections.size()));
            for (String section : sections) {
                updateField(digest, "diff", section);
            }
            for (Map.Entry<String, String> entry : new TreeMap<>(reviewInputs).entrySet()) {
                updateField(digest, entry.getKey(), entry.getValue() == null ? "" : entry.getValue());
            }
            return bytesToHex(digest.digest());
        } catch (NoSuchAlgorithmException e) {
            // SHA-256 is guaranteed by the JVM spec — should never happen
            throw new IllegalStateException("SHA-256 not available", e);
        }
    }

    private static List<String> canonicalSections(String diff) {
        String normalized = removeTerminalLineEndings(
                diff.replace("\r\n", "\n").replace("\r", "\n"));
        List<String> sections = new ArrayList<>();
        int sectionStart = normalized.startsWith("diff --git ") ? 0 : -1;
        int searchFrom = 1;
        while (true) {
            int next = normalized.indexOf("\ndiff --git ", searchFrom);
            if (next < 0) break;
            if (sectionStart >= 0) sections.add(normalized.substring(sectionStart, next));
            sectionStart = next + 1;
            searchFrom = sectionStart + 1;
        }
        if (sectionStart >= 0) {
            sections.add(removeTerminalLineEndings(normalized.substring(sectionStart)));
        } else if (!normalized.isBlank()) {
            sections.add(normalized);
        }
        sections.removeIf(String::isBlank);
        Collections.sort(sections);
        return sections;
    }

    private static String removeTerminalLineEndings(String value) {
        int end = value.length();
        while (end > 0 && value.charAt(end - 1) == '\n') {
            end--;
        }
        return value.substring(0, end);
    }

    private static void updateField(MessageDigest digest, String key, String value) {
        byte[] keyBytes = key.getBytes(StandardCharsets.UTF_8);
        byte[] valueBytes = value.getBytes(StandardCharsets.UTF_8);
        digest.update(Integer.toString(keyBytes.length).getBytes(StandardCharsets.US_ASCII));
        digest.update((byte) ':');
        digest.update(keyBytes);
        digest.update((byte) '=');
        digest.update(Integer.toString(valueBytes.length).getBytes(StandardCharsets.US_ASCII));
        digest.update((byte) ':');
        digest.update(valueBytes);
        digest.update((byte) '\n');
    }

    private static String bytesToHex(byte[] bytes) {
        StringBuilder sb = new StringBuilder(bytes.length * 2);
        for (byte b : bytes) {
            sb.append(String.format("%02x", b & 0xff));
        }
        return sb.toString();
    }
}
