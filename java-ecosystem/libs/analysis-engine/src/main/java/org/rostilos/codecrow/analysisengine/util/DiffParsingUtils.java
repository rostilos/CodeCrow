package org.rostilos.codecrow.analysisengine.util;

import java.util.*;
import java.util.regex.Matcher;
import java.util.regex.Pattern;

/**
 * Stateless utility methods for parsing unified diffs.
 * <p>
 * Extracts file paths, splits multi-file diffs into per-file sections,
 * parses hunk headers, and maps old line numbers to new positions.
 */
public final class DiffParsingUtils {

    private DiffParsingUtils() {
        // utility class — no instantiation
    }

    /** Matches {@code diff --git a/path b/path} headers. */
    public static final Pattern DIFF_GIT_PATTERN =
            Pattern.compile("^diff --git\\s+a/(\\S+)\\s+b/(\\S+)");

    /**
     * Pattern for unified-diff hunk headers:
     * {@code @@ -oldStart[,oldCount] +newStart[,newCount] @@}
     */
    public static final Pattern HUNK_HEADER = Pattern.compile(
            "^@@\\s+-(?:(\\d+))(?:,(\\d+))?\\s+\\+(?:(\\d+))(?:,(\\d+))?\\s+@@");

    // ───────────────────────────── File-path extraction ─────────────────────────

    /**
     * Extract the set of changed file paths from a raw unified diff.
     *
     * @param rawDiff full unified diff text (may be {@code null})
     * @return set of file paths (using the {@code b/…} destination path)
     */
    public static Set<String> parseFilePathsFromDiff(String rawDiff) {
        Set<String> files = new HashSet<>();
        if (rawDiff == null || rawDiff.isBlank()) {
            return files;
        }
        String[] lines = rawDiff.split("\\r?\\n");
        for (String line : lines) {
            Matcher m = DIFF_GIT_PATTERN.matcher(line);
            if (m.find()) {
                String path = m.group(2);
                if (path != null && !path.isBlank()) {
                    files.add(path);
                }
            }
        }
        return files;
    }

    // ─────────────────────────── Per-file diff splitting ────────────────────────

    /**
     * Split a multi-file unified diff into per-file sections.
     *
     * @return map of filePath → that file's diff text
     *         (from {@code diff --git} to the next header or EOF)
     */
    public static Map<String, String> splitDiffByFile(String rawDiff) {
        Map<String, String> result = new LinkedHashMap<>();
        String[] lines = rawDiff.split("\\r?\\n");
        String currentFile = null;
        StringBuilder currentSection = new StringBuilder();

        for (String line : lines) {
            Matcher m = DIFF_GIT_PATTERN.matcher(line);
            if (m.find()) {
                if (currentFile != null) {
                    result.put(currentFile, currentSection.toString());
                }
                currentFile = m.group(2);
                currentSection = new StringBuilder();
            }
            if (currentFile != null) {
                currentSection.append(line).append("\n");
            }
        }
        if (currentFile != null) {
            result.put(currentFile, currentSection.toString());
        }
        return result;
    }

    // ──────────────────────────── Hunk header parsing ──────────────────────────

    /**
     * Parse all hunk headers from a single-file diff section.
     *
     * @return list of {@code int[4]}: {oldStart, oldCount, newStart, newCount}
     */
    public static List<int[]> parseHunks(String fileDiff) {
        List<int[]> hunks = new ArrayList<>();
        for (String line : fileDiff.split("\\r?\\n")) {
            Matcher m = HUNK_HEADER.matcher(line);
            if (m.find()) {
                int oldStart = Integer.parseInt(m.group(1));
                int oldCount = m.group(2) != null ? Integer.parseInt(m.group(2)) : 1;
                int newStart = Integer.parseInt(m.group(3));
                int newCount = m.group(4) != null ? Integer.parseInt(m.group(4)) : 1;
                hunks.add(new int[]{oldStart, oldCount, newStart, newCount});
            }
        }
        return hunks;
    }

    // ─────────────────────────── Line-number mapping ───────────────────────────

    /**
     * Map an old line number to its new position after the diff was applied.
     * <ul>
     *   <li>Before the first hunk → unchanged.</li>
     *   <li>Between hunks → shift by cumulative offset.</li>
     *   <li>Inside a hunk → walk the hunk body line-by-line.</li>
     *   <li>Deleted line → return old number unchanged (AI reconciliation handles).</li>
     * </ul>
     *
     * @param oldLine  original 1-based line number
     * @param hunks    parsed hunk headers
     * @param fileDiff full diff section for this file (needed for line-by-line walk)
     * @return new 1-based line number, or original if the line was deleted
     */
    public static int mapLineNumber(int oldLine, List<int[]> hunks, String fileDiff) {
        int cumulativeOffset = 0;

        for (int[] hunk : hunks) {
            int oldStart = hunk[0];
            int oldCount = hunk[1];
            int newStart = hunk[2];
            int newCount = hunk[3];
            int oldEnd = oldStart + oldCount - 1;

            if (oldLine < oldStart) {
                return oldLine + cumulativeOffset;
            }
            if (oldLine <= oldEnd) {
                return mapLineInsideHunk(oldLine, oldStart, newStart, fileDiff);
            }
            cumulativeOffset += (newCount - oldCount);
        }
        return oldLine + cumulativeOffset;
    }

    /**
     * Walk a hunk body line-by-line to map an old line number to its new position.
     * <p>
     * Context lines advance both counters; {@code -} lines advance only old;
     * {@code +} lines advance only new.  If the target old line was deleted,
     * returns the original line number unchanged.
     */
    static int mapLineInsideHunk(int targetOldLine, int hunkOldStart,
                                 int hunkNewStart, String fileDiff) {
        String[] lines = fileDiff.split("\\r?\\n");
        int oldCursor = hunkOldStart;
        int newCursor = hunkNewStart;
        boolean inTargetHunk = false;

        for (String line : lines) {
            Matcher hm = HUNK_HEADER.matcher(line);
            if (hm.find()) {
                int thisOldStart = Integer.parseInt(hm.group(1));
                if (thisOldStart == hunkOldStart) {
                    inTargetHunk = true;
                    oldCursor = hunkOldStart;
                    newCursor = hunkNewStart;
                    continue;
                } else if (inTargetHunk) {
                    break;
                }
                continue;
            }

            if (!inTargetHunk) continue;

            if (line.startsWith("-")) {
                if (oldCursor == targetOldLine) {
                    return targetOldLine;
                }
                oldCursor++;
            } else if (line.startsWith("+")) {
                newCursor++;
            } else {
                if (oldCursor == targetOldLine) {
                    return newCursor;
                }
                oldCursor++;
                newCursor++;
            }
        }
        return targetOldLine;
    }
}
