package org.rostilos.codecrow.core.util.tracking;

import java.nio.charset.StandardCharsets;
import java.security.MessageDigest;
import java.security.NoSuchAlgorithmException;
import java.util.regex.Pattern;

/**
 * Computes a stable fingerprint for an issue that survives line shifts and minor edits.
 * <p>
 * The fingerprint is a SHA-256 hash of three components:
 * <ol>
 *   <li><b>Category</b> — the issue category enum name (e.g. "SECURITY", "BUG_RISK")</li>
 *   <li><b>Line hash</b> — MD5 of the source line content (content anchor)</li>
 *   <li><b>Normalized title</b> — the issue title with volatile fragments stripped</li>
 * </ol>
 * <p>
 * Title normalization removes:
 * <ul>
 *   <li>Line number references (e.g. "on line 42", "line 87", "L42")</li>
 *   <li>Standalone numbers (likely line/column references)</li>
 *   <li>Extra whitespace</li>
 * </ul>
 * This ensures that the same logical issue detected at different line positions
 * produces the same fingerprint, enabling stable tracking across analyses.
 *
 * <h3>Thread safety</h3>
 * All methods are stateless and safe to call from any thread.
 */
public final class IssueFingerprint {

    private IssueFingerprint() { /* utility class */ }

    /**
     * Pattern to remove line-number references from titles.
     * Matches: "line 42", "on line 42", "L42", "at line 42", "lines 10-20"
     */
    private static final Pattern LINE_REF_PATTERN = Pattern.compile(
            "(?i)(?:(?:on|at)\\s+)?lines?\\s*\\d+(?:\\s*-\\s*\\d+)?|L\\d+",
            Pattern.CASE_INSENSITIVE
    );

    /**
     * Pattern to remove standalone numbers (likely line/column references).
     * Only matches numbers that are NOT part of identifiers (preceded/followed by word chars).
     */
    private static final Pattern STANDALONE_NUM_PATTERN = Pattern.compile(
            "(?<![\\w.])\\d+(?![\\w.])"
    );

    /**
     * Compute the issue fingerprint from its components.
     *
     * @param category the issue category name (e.g. "SECURITY"), may be {@code null}
     * @param lineHash MD5 of the source line content, may be {@code null}
     * @param title    the issue title, may be {@code null}
     * @return 64-char lowercase hex SHA-256 string
     */
    public static String compute(String category, String lineHash, String title) {
        String normalizedCategory = category != null ? category.toUpperCase().trim() : "UNKNOWN";
        String safeLineHash = lineHash != null ? lineHash : "no_hash";
        String normalizedTitle = normalizeTitle(title);

        String composite = normalizedCategory + ":" + safeLineHash + ":" + normalizedTitle;
        return sha256Hex(composite.getBytes(StandardCharsets.UTF_8));
    }

    /**
     * Compute fingerprint from an issue category enum, line hash, and title.
     * Convenience overload that accepts the enum directly.
     *
     * @param category the issue category enum
     * @param lineHash MD5 of the source line content
     * @param title    the issue title
     * @return 64-char lowercase hex SHA-256 string
     */
    public static String compute(Enum<?> category, String lineHash, String title) {
        return compute(category != null ? category.name() : null, lineHash, title);
    }

    /**
     * Compute a <b>category-agnostic</b> content fingerprint.
     * <p>
     * This fingerprint intentionally omits the issue category, making it resilient
     * to AI classification instability (e.g. the same "double semicolon" issue
     * classified as STYLE by one analysis and CODE_QUALITY by another).
     * <p>
     * Used as a secondary deduplication key when the primary category-aware
     * fingerprint fails to match due to category drift.
     *
     * @param lineHash MD5 of the source line content, may be {@code null}
     * @param title    the issue title, may be {@code null}
     * @return 64-char lowercase hex SHA-256 string
     */
    public static String computeContentFingerprint(String lineHash, String title) {
        String safeLineHash = lineHash != null ? lineHash : "no_hash";
        String normalizedTitle = normalizeTitle(title);
        String composite = safeLineHash + ":" + normalizedTitle;
        return sha256Hex(composite.getBytes(StandardCharsets.UTF_8));
    }

    /**
     * Normalize a title for stable fingerprinting.
     * <p>
     * Steps:
     * <ol>
     *   <li>Lowercase</li>
     *   <li>Remove line-number references ("on line 42", "L42", etc.)</li>
     *   <li>Remove standalone numbers (likely positional references)</li>
     *   <li>Collapse whitespace to single space</li>
     *   <li>Strip leading/trailing whitespace</li>
     * </ol>
     *
     * @param title the raw title
     * @return normalized title, or empty string if {@code null}
     */
    public static String normalizeTitle(String title) {
        if (title == null || title.isBlank()) {
            return "";
        }

        String result = title.toLowerCase();
        // Remove line number references
        result = LINE_REF_PATTERN.matcher(result).replaceAll("");
        // Remove standalone numbers
        result = STANDALONE_NUM_PATTERN.matcher(result).replaceAll("");
        // Collapse whitespace
        result = result.replaceAll("\\s+", " ").trim();
        return result;
    }

    private static String sha256Hex(byte[] data) {
        try {
            MessageDigest digest = MessageDigest.getInstance("SHA-256");
            byte[] hash = digest.digest(data);
            StringBuilder sb = new StringBuilder(64);
            for (byte b : hash) {
                sb.append(String.format("%02x", b & 0xff));
            }
            return sb.toString();
        } catch (NoSuchAlgorithmException e) {
            // SHA-256 is guaranteed by the JVM spec
            throw new IllegalStateException("SHA-256 not available", e);
        }
    }
}
