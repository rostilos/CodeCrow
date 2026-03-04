package org.rostilos.codecrow.core.util.tracking;

import java.nio.charset.StandardCharsets;
import java.security.MessageDigest;
import java.security.NoSuchAlgorithmException;
import java.util.*;

/**
 * An ordered sequence of content hashes, one per source line.
 * <p>
 * Each line is hashed after whitespace normalization (strip leading/trailing, collapse internal
 * whitespace to a single space). This makes hashes robust against formatting changes.
 * <p>
 * Maintains a reverse index from hash → set of line numbers, enabling efficient lookup
 * of "where did this content move to?" during issue tracking.
 * <p>
 * Inspired by SonarQube's {@code LineHashSequence} / {@code SourceLineHashesComputer},
 * but simplified: uses a single MD5 per line without the block-hash windowing.
 *
 * <h3>Thread safety</h3>
 * Instances are immutable after construction and safe to share across threads.
 */
public final class LineHashSequence {

    /** 1-indexed: hashes[0] is unused, hashes[1] corresponds to line 1. */
    private final String[] hashes;

    /** Reverse index: hash → set of 1-based line numbers that produce this hash. */
    private final Map<String, Set<Integer>> hashToLines;

    /** Total number of source lines (== hashes.length - 1). */
    private final int lineCount;

    private LineHashSequence(String[] hashes, Map<String, Set<Integer>> hashToLines) {
        this.hashes = hashes;
        this.hashToLines = hashToLines;
        this.lineCount = hashes.length - 1;
    }

    /**
     * Build a {@code LineHashSequence} from raw file content.
     *
     * @param fileContent the full file content (may use any line ending style)
     * @return a new sequence, never {@code null}
     * @throws IllegalArgumentException if {@code fileContent} is {@code null}
     */
    public static LineHashSequence from(String fileContent) {
        if (fileContent == null) {
            throw new IllegalArgumentException("fileContent must not be null");
        }

        String[] lines = fileContent.split("\\r?\\n", -1);
        // Remove trailing empty element if file ends with newline
        if (lines.length > 0 && lines[lines.length - 1].isEmpty()) {
            lines = Arrays.copyOf(lines, lines.length - 1);
        }

        // hashes[0] is a sentinel (unused); hashes[1..N] correspond to lines 1..N
        String[] hashes = new String[lines.length + 1];
        hashes[0] = "";
        Map<String, Set<Integer>> reverseIndex = new HashMap<>();

        for (int i = 0; i < lines.length; i++) {
            int lineNumber = i + 1;
            String hash = hashLine(lines[i]);
            hashes[lineNumber] = hash;
            reverseIndex.computeIfAbsent(hash, k -> new LinkedHashSet<>()).add(lineNumber);
        }

        return new LineHashSequence(hashes, reverseIndex);
    }

    /**
     * Returns an empty sequence (zero lines). Useful as a null-object for deleted files.
     */
    public static LineHashSequence empty() {
        return new LineHashSequence(new String[]{""}, Collections.emptyMap());
    }

    /**
     * Get the content hash for a specific line.
     *
     * @param line 1-based line number
     * @return the MD5 hex hash, or {@code null} if line is out of range
     */
    public String getHashForLine(int line) {
        if (line < 1 || line > lineCount) {
            return null;
        }
        return hashes[line];
    }

    /**
     * Get all line numbers that produce the given hash.
     * Enables "where did this content move to?" lookups.
     *
     * @param hash the MD5 hex hash to look up
     * @return unmodifiable set of 1-based line numbers (empty if hash not found)
     */
    public Set<Integer> getLinesForHash(String hash) {
        if (hash == null) {
            return Collections.emptySet();
        }
        Set<Integer> lines = hashToLines.get(hash);
        return lines != null ? Collections.unmodifiableSet(lines) : Collections.emptySet();
    }

    /**
     * Compute a context hash by combining the hashes of a window of lines around the target.
     * This provides a more stable anchor than a single-line hash for multi-line patterns.
     *
     * @param line   1-based center line
     * @param radius number of lines above and below to include (e.g. 2 → ±2 lines = 5-line window)
     * @return MD5 hex hash of the concatenated line hashes in the window, or {@code null} if line is out of range
     */
    public String getContextHash(int line, int radius) {
        if (line < 1 || line > lineCount) {
            return null;
        }
        int start = Math.max(1, line - radius);
        int end = Math.min(lineCount, line + radius);

        StringBuilder combined = new StringBuilder();
        for (int i = start; i <= end; i++) {
            combined.append(hashes[i]);
        }
        return md5Hex(combined.toString().getBytes(StandardCharsets.UTF_8));
    }

    /**
     * Compute a hash covering an exact range of lines {@code [startLine, endLine]}.
     * <p>
     * Unlike {@link #getContextHash(int, int)} which uses a symmetric radius,
     * this method hashes exactly the specified range — ideal when actual scope
     * boundaries (e.g. function start/end) are known.
     *
     * @param startLine 1-based inclusive start
     * @param endLine   1-based inclusive end
     * @return MD5 hex hash of the concatenated line hashes, or {@code null} if range is invalid
     */
    public String getRangeHash(int startLine, int endLine) {
        if (startLine < 1 || endLine < startLine || startLine > lineCount) {
            return null;
        }
        int clampedEnd = Math.min(endLine, lineCount);
        StringBuilder combined = new StringBuilder();
        for (int i = startLine; i <= clampedEnd; i++) {
            combined.append(hashes[i]);
        }
        return md5Hex(combined.toString().getBytes(StandardCharsets.UTF_8));
    }

    /**
     * @return total number of lines in the file
     */
    public int getLineCount() {
        return lineCount;
    }

    /**
     * Check if a specific hash exists anywhere in the file.
     *
     * @param hash the hash to check
     * @return true if at least one line produces this hash
     */
    public boolean containsHash(String hash) {
        return hash != null && hashToLines.containsKey(hash);
    }

    /**
     * Find the closest line to {@code targetLine} that produces the given hash.
     * Useful for re-anchoring an issue after code shifts.
     *
     * @param hash       the hash to find
     * @param targetLine the preferred line (closest match wins)
     * @return the closest line number, or -1 if hash not found
     */
    public int findClosestLineForHash(String hash, int targetLine) {
        Set<Integer> candidates = getLinesForHash(hash);
        if (candidates.isEmpty()) {
            return -1;
        }
        int closest = -1;
        int minDist = Integer.MAX_VALUE;
        for (int candidate : candidates) {
            int dist = Math.abs(candidate - targetLine);
            if (dist < minDist) {
                minDist = dist;
                closest = candidate;
            }
        }
        return closest;
    }

    // ─────────────── Hashing ───────────────

    /**
     * Hash a single source line after whitespace normalization.
     * <p>
     * Normalization: strip leading and trailing whitespace, collapse all internal
     * whitespace sequences to a single space. This makes the hash invariant to
     * indentation changes and trivial reformatting.
     */
    public static String hashLine(String line) {
        String normalized = normalizeLine(line);
        return md5Hex(normalized.getBytes(StandardCharsets.UTF_8));
    }

    /**
     * Normalize a source line for hashing:
     * 1. Strip leading/trailing whitespace
     * 2. Collapse internal whitespace to single space
     */
    static String normalizeLine(String line) {
        if (line == null || line.isEmpty()) {
            return "";
        }
        return line.strip().replaceAll("\\s+", " ");
    }

    private static String md5Hex(byte[] data) {
        try {
            MessageDigest md = MessageDigest.getInstance("MD5");
            byte[] digest = md.digest(data);
            StringBuilder sb = new StringBuilder(32);
            for (byte b : digest) {
                sb.append(String.format("%02x", b & 0xff));
            }
            return sb.toString();
        } catch (NoSuchAlgorithmException e) {
            // MD5 is guaranteed by the JVM spec
            throw new IllegalStateException("MD5 not available", e);
        }
    }
}
