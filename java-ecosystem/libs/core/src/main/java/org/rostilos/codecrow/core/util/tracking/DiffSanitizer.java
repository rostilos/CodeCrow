package org.rostilos.codecrow.core.util.tracking;

/**
 * Utility class for cleaning and validating diff format strings from AI responses.
 * <p>
 * LLMs frequently wrap suggested-fix diffs in markdown code-block markers (e.g.
 * {@code ```diff} / {@code ```}) or include other formatting artifacts.
 * This sanitizer strips those markers and validates that the resulting diff
 * contains the expected unified-diff structure.
 *
 * <h3>Thread safety</h3>
 * All methods are stateless and safe to call from any thread.
 */
public final class DiffSanitizer {

    /** Placeholder value set when no fix is available. */
    public static final String NO_FIX_PLACEHOLDER = "No suggested fix provided";

    /** Placeholder value set when no fix description is available. */
    public static final String NO_FIX_DESCRIPTION_PLACEHOLDER = "No suggested fix description provided";

    private DiffSanitizer() { /* utility class */ }

    /**
     * Check whether a suggested-fix description contains a real value (not null,
     * empty, or the default placeholder).
     *
     * @param description the suggested fix description, may be {@code null}
     * @return {@code true} if the description is meaningful
     */
    public static boolean hasRealFixDescription(String description) {
        if (description == null || description.isBlank()) {
            return false;
        }
        return !NO_FIX_DESCRIPTION_PLACEHOLDER.equals(description.trim());
    }

    /**
     * Clean markdown code-block markers and other formatting artifacts from a diff string.
     * <p>
     * Strips lines that are exactly {@code ```}, {@code ```diff}, {@code ```java},
     * {@code ```python}, or any other {@code ```<lang>} variant. Also trims trailing
     * whitespace from the result.
     *
     * @param diff the raw diff string, may be {@code null}
     * @return the cleaned diff, or the original value if {@code null}/empty
     */
    public static String cleanDiffFormat(String diff) {
        if (diff == null || diff.isEmpty()) {
            return diff;
        }

        if (NO_FIX_PLACEHOLDER.equals(diff)) {
            return diff;
        }

        String[] lines = diff.split("\n", -1);
        StringBuilder cleaned = new StringBuilder(diff.length());
        boolean first = true;

        for (String line : lines) {
            String trimmed = line.trim();

            // Skip markdown code-block fences: ```, ```diff, ```java, etc.
            if (isCodeFence(trimmed)) {
                continue;
            }

            if (!first) {
                cleaned.append('\n');
            }
            cleaned.append(line);
            first = false;
        }

        return cleaned.toString().stripTrailing();
    }

    /**
     * Check whether a diff string contains minimal unified-diff markers.
     * <p>
     * A valid diff should contain at least one of the unified-diff indicators:
     * {@code ---}, {@code +++}, or {@code @@} hunk headers, or context/change
     * lines starting with {@code -} or {@code +} (on their own line).
     *
     * @param diff the diff string, may be {@code null}
     * @return {@code true} if the string looks like a valid diff
     */
    public static boolean isValidDiffFormat(String diff) {
        if (diff == null || diff.isBlank()) {
            return false;
        }

        if (NO_FIX_PLACEHOLDER.equals(diff)) {
            return false;
        }

        if (diff.strip().length() < 10) {
            return false;
        }

        // Check for any diff marker in the content
        return diff.contains("---") || diff.contains("+++") || diff.contains("@@")
                || diff.contains("\n-") || diff.contains("\n+");
    }

    /**
     * Convenience method: clean the diff and, if the result is not a valid diff
     * format, return {@code null} instead.
     *
     * @param diff the raw diff string
     * @return the cleaned diff if valid, or {@code null}
     */
    public static String cleanAndValidate(String diff) {
        String cleaned = cleanDiffFormat(diff);
        return isValidDiffFormat(cleaned) ? cleaned : null;
    }

    // ── Internal ─────────────────────────────────────────────────────────

    /**
     * Determine whether a trimmed line is a markdown code fence.
     * Matches: {@code ```}, {@code ```diff}, {@code ```java}, etc.
     */
    private static boolean isCodeFence(String trimmedLine) {
        if (trimmedLine.equals("```")) {
            return true;
        }
        // Match ```<lang> where <lang> is letters only, e.g. ```diff, ```java
        if (trimmedLine.startsWith("```") && trimmedLine.length() <= 20) {
            String suffix = trimmedLine.substring(3);
            return suffix.chars().allMatch(c -> Character.isLetter(c) || c == '-');
        }
        return false;
    }
}
