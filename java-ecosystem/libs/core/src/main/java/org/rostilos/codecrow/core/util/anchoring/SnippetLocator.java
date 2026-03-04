package org.rostilos.codecrow.core.util.anchoring;

import org.rostilos.codecrow.core.util.tracking.LineHashSequence;

import java.util.Arrays;

/**
 * Locates a code snippet within file content, returning the best-matching line number.
 * <p>
 * Uses multiple strategies in decreasing order of precision:
 * <ol>
 *   <li><b>HASH_EXACT</b> — MD5-hash match via {@link LineHashSequence} (fastest, O(1) lookup)</li>
 *   <li><b>TRIMMED_CONTAINS</b> — substring/contains match for partial or trimmed snippets</li>
 *   <li><b>MULTI_LINE</b> — contiguous block match for multi-line snippets</li>
 * </ol>
 * <p>
 * For every strategy, if multiple candidates exist, the one closest to the
 * LLM's hint line is chosen (the LLM's estimate is often in the right neighborhood).
 *
 * <h3>Thread safety</h3>
 * All methods are stateless and safe to call from any thread.
 */
public final class SnippetLocator {

    private SnippetLocator() {}

    /**
     * Result of snippet location.
     *
     * @param line       1-based line number where the snippet was found
     * @param endLine    1-based last line (for multi-line snippets), or same as {@code line}
     * @param confidence 0.0–1.0 indicating match quality
     * @param strategy   which strategy produced the match
     */
    public record LocateResult(int line, int endLine, float confidence, Strategy strategy) {}

    /** Which matching strategy produced the result. */
    public enum Strategy {
        /** MD5-hash of normalized line matched exactly. */
        HASH_EXACT,
        /** Normalized snippet is a substring of a file line (or vice versa). */
        TRIMMED_CONTAINS,
        /** Multiple contiguous snippet lines matched a block in the file. */
        MULTI_LINE,
        /** No match found. */
        NOT_FOUND
    }

    /**
     * Find where a code snippet occurs in the given file content.
     *
     * @param snippet     the code snippet to locate (may be single or multi-line)
     * @param fileContent full file content
     * @param hintLine    the LLM-reported line number (used as tie-breaker for multiple matches)
     * @return location result; check {@code strategy() != NOT_FOUND} to see if it succeeded
     */
    public static LocateResult locate(String snippet, String fileContent, int hintLine) {
        if (snippet == null || snippet.isBlank() || fileContent == null || fileContent.isEmpty()) {
            return notFound(hintLine);
        }

        String[] fileLines = fileContent.split("\\r?\\n", -1);
        String[] snippetLines = snippet.split("\\r?\\n");

        // Filter out blank snippet lines
        String[] nonBlank = Arrays.stream(snippetLines)
                .filter(l -> l != null && !l.isBlank())
                .toArray(String[]::new);

        if (nonBlank.length == 0) {
            return notFound(hintLine);
        }

        // ── Strategy 1: Hash-exact match via LineHashSequence ──────────
        // Hash the first non-blank snippet line and look it up in the file's
        // reverse index. This is O(1) and handles whitespace normalization.
        LineHashSequence lineHashes = LineHashSequence.from(fileContent);
        String firstHash = LineHashSequence.hashLine(nonBlank[0]);
        int hashMatch = lineHashes.findClosestLineForHash(firstHash, Math.max(1, hintLine));

        if (hashMatch > 0) {
            if (nonBlank.length > 1) {
                // Multi-line snippet: verify subsequent lines match contiguously
                int endLine = verifyContiguousHashMatch(nonBlank, fileLines, hashMatch);
                if (endLine > 0) {
                    return new LocateResult(hashMatch, endLine, 1.0f, Strategy.HASH_EXACT);
                }
            }
            return new LocateResult(hashMatch, hashMatch, 1.0f, Strategy.HASH_EXACT);
        }

        // ── Strategy 2: Trimmed-contains match ────────────────────────
        // The snippet might be a trimmed substring of a file line, or vice versa.
        // Only for non-trivial snippets (≥8 chars) to avoid false positives.
        String normalizedSnippet = normalize(nonBlank[0]);
        if (normalizedSnippet.length() >= 8) {
            int bestLine = -1;
            int bestDist = Integer.MAX_VALUE;

            for (int i = 0; i < fileLines.length; i++) {
                String normalizedFileLine = normalize(fileLines[i]);
                if (normalizedFileLine.isEmpty()) continue;

                boolean matches = normalizedFileLine.contains(normalizedSnippet)
                        || normalizedSnippet.contains(normalizedFileLine);

                if (matches) {
                    // Require the shorter to be at least 50% of the longer to avoid noise
                    int shorter = Math.min(normalizedFileLine.length(), normalizedSnippet.length());
                    int longer = Math.max(normalizedFileLine.length(), normalizedSnippet.length());
                    if (shorter * 100 / longer >= 50) {
                        int dist = Math.abs((i + 1) - hintLine);
                        if (dist < bestDist) {
                            bestDist = dist;
                            bestLine = i + 1;
                        }
                    }
                }
            }

            if (bestLine > 0) {
                return new LocateResult(bestLine, bestLine, 0.75f, Strategy.TRIMMED_CONTAINS);
            }
        }

        // ── Strategy 3: Multi-line block search ───────────────────────
        // Try to find a contiguous block of ≥2 snippet lines in the file.
        if (nonBlank.length >= 2) {
            int matchStart = findMultiLineBlock(nonBlank, fileLines, hintLine);
            if (matchStart > 0) {
                int endLine = Math.min(matchStart + nonBlank.length - 1, fileLines.length);
                return new LocateResult(matchStart, endLine, 0.85f, Strategy.MULTI_LINE);
            }
        }

        // ── No match ──────────────────────────────────────────────────
        return notFound(hintLine);
    }

    // ─── Internal helpers ─────────────────────────────────────────────

    private static LocateResult notFound(int hintLine) {
        return new LocateResult(Math.max(1, hintLine), Math.max(1, hintLine), 0.0f, Strategy.NOT_FOUND);
    }

    /**
     * Verify that snippet lines match contiguously in the file starting at startLine (1-based).
     * Returns the 1-based end line if match, or -1.
     */
    private static int verifyContiguousHashMatch(String[] snippetLines, String[] fileLines, int startLine) {
        int fileIdx = startLine - 1; // 0-based
        int matched = 0;

        for (String sl : snippetLines) {
            if (sl == null || sl.isBlank()) continue;
            String slHash = LineHashSequence.hashLine(sl);

            // Allow up to 2 lines of "slack" (blank/comment lines in the file)
            boolean found = false;
            for (int offset = 0; offset <= 2 && fileIdx + offset < fileLines.length; offset++) {
                if (slHash.equals(LineHashSequence.hashLine(fileLines[fileIdx + offset]))) {
                    fileIdx = fileIdx + offset + 1;
                    matched++;
                    found = true;
                    break;
                }
            }
            if (!found) break;
        }

        // Require at least half the non-blank snippet lines to match
        long nonBlankCount = Arrays.stream(snippetLines)
                .filter(l -> l != null && !l.isBlank()).count();
        if (matched >= Math.max(2, nonBlankCount / 2)) {
            return startLine + matched - 1; // 1-based end line
        }
        return -1;
    }

    /**
     * Find a contiguous block of snippet lines in the file.
     * Returns 1-based start line, or -1 if not found.
     */
    private static int findMultiLineBlock(String[] snippetLines, String[] fileLines, int hintLine) {
        String firstNormalized = normalize(snippetLines[0]);
        if (firstNormalized.isEmpty()) return -1;

        int bestStart = -1;
        int bestDist = Integer.MAX_VALUE;
        int bestMatchCount = 0;

        for (int i = 0; i < fileLines.length; i++) {
            if (!normalize(fileLines[i]).equals(firstNormalized)) continue;

            // First line matches — try subsequent lines
            int matchCount = 1;
            int fi = i + 1;
            for (int si = 1; si < snippetLines.length && fi < fileLines.length; si++) {
                if (snippetLines[si] == null || snippetLines[si].isBlank()) continue;

                // Allow up to 1 extra line gap (for blank lines in file not in snippet)
                boolean found = false;
                for (int gap = 0; gap <= 1 && fi + gap < fileLines.length; gap++) {
                    if (normalize(fileLines[fi + gap]).equals(normalize(snippetLines[si]))) {
                        fi = fi + gap + 1;
                        matchCount++;
                        found = true;
                        break;
                    }
                }
                if (!found) break;
            }

            if (matchCount >= 2) {
                int dist = Math.abs((i + 1) - hintLine);
                if (matchCount > bestMatchCount || (matchCount == bestMatchCount && dist < bestDist)) {
                    bestStart = i + 1; // 1-based
                    bestDist = dist;
                    bestMatchCount = matchCount;
                }
            }
        }

        return bestStart;
    }

    /**
     * Normalize a line for comparison: strip leading/trailing whitespace, collapse internal whitespace.
     */
    static String normalize(String line) {
        if (line == null || line.isEmpty()) return "";
        return line.strip().replaceAll("\\s+", " ");
    }
}
