package org.rostilos.codecrow.analysisengine.util;

import java.nio.charset.StandardCharsets;
import java.util.Locale;
import java.util.Set;

/**
 * Shared host-level classification for files that cannot provide text context.
 *
 * <p>This is intentionally a conservative binary/non-text blacklist rather
 * than a source-language allowlist. Language and framework plugins remain free
 * to decide which text files are useful; the host only prevents known binary
 * assets from entering text acquisition and indexing paths.</p>
 */
public final class TextFileEligibility {

    public static final long MAX_TEXT_CONTENT_BYTES = 10L * 1024L * 1024L;

    private static final Set<String> EXCLUDED_EXTENSIONS = Set.of(
            // Raster images. SVG is XML source and remains eligible.
            ".png", ".jpg", ".jpeg", ".gif", ".bmp", ".ico", ".webp", ".tiff", ".tif",
            // Fonts
            ".woff", ".woff2", ".ttf", ".otf", ".eot",
            // Compiled / bytecode
            ".class", ".pyc", ".pyo", ".o", ".obj", ".dll", ".so", ".dylib", ".exe",
            ".war", ".ear", ".nar",
            // Archives
            ".jar", ".zip", ".tar", ".gz", ".bz2", ".xz", ".7z", ".rar",
            // Documents / media
            ".pdf", ".doc", ".docx", ".xls", ".xlsx", ".ppt", ".pptx",
            ".mp3", ".mp4", ".avi", ".mov", ".wav", ".flac", ".ogg", ".webm",
            // Data blobs / certificates
            ".bin", ".dat", ".db", ".sqlite", ".sqlite3",
            ".p12", ".pfx", ".jks", ".keystore", ".der", ".cer",
            // Lock files and other binary build products
            ".lockb", ".wasm"
    );

    private TextFileEligibility() {
    }

    public static boolean isTextCandidate(String path) {
        if (path == null || path.isBlank()) {
            return false;
        }
        String normalized = path.toLowerCase(Locale.ROOT);
        return EXCLUDED_EXTENSIONS.stream().noneMatch(normalized::endsWith);
    }

    /**
     * Applies the same bounded-text expectation to provider responses whose
     * extension alone does not reveal whether they are indexable.
     */
    public static boolean isBoundedTextContent(String content) {
        if (content == null || content.indexOf('\0') >= 0) {
            return false;
        }
        if (content.length() > MAX_TEXT_CONTENT_BYTES) {
            return false;
        }
        return content.getBytes(StandardCharsets.UTF_8).length <= MAX_TEXT_CONTENT_BYTES;
    }
}
