package org.rostilos.codecrow.vcsclient.model;

import java.nio.charset.StandardCharsets;

/**
 * Provider-neutral outcome for one exact-source request.
 *
 * <p>Unlike the legacy path-to-content map, this contract preserves why a
 * requested path has no content. Callers can therefore distinguish a stable
 * non-indexable disposition from a transient acquisition failure.</p>
 */
public record VcsFileContentResult(
        String path,
        String content,
        long sizeBytes,
        Status status,
        String diagnostic
) {
    public VcsFileContentResult {
        if (path == null || path.isBlank()) {
            throw new IllegalArgumentException("file path is required");
        }
        if (sizeBytes < 0) {
            throw new IllegalArgumentException("file size cannot be negative");
        }
        if (status == null) {
            throw new IllegalArgumentException("file content status is required");
        }
    }

    public static VcsFileContentResult available(String path, String content) {
        long size = content == null ? 0 : content.getBytes(StandardCharsets.UTF_8).length;
        return new VcsFileContentResult(path, content, size, Status.AVAILABLE, null);
    }

    public static VcsFileContentResult skipped(
            String path,
            long sizeBytes,
            Status status,
            String diagnostic) {
        if (status == Status.AVAILABLE) {
            throw new IllegalArgumentException("available content must use available()");
        }
        return new VcsFileContentResult(path, null, sizeBytes, status, diagnostic);
    }

    public boolean available() {
        return status == Status.AVAILABLE && content != null;
    }

    public enum Status {
        AVAILABLE,
        TOO_LARGE,
        UNSUPPORTED,
        FETCH_FAILED
    }
}
