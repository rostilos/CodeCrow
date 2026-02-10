package org.rostilos.codecrow.analysisengine.dto.request.ai.enrichment;

/**
 * DTO representing the content of a single file retrieved from VCS.
 * Used for file enrichment during PR analysis to provide full file context.
 */
public record FileContentDto(
        String path,
        String content,
        long sizeBytes,
        boolean skipped,
        String skipReason
) {
    /**
     * Create a successful file content result.
     */
    public static FileContentDto of(String path, String content) {
        return new FileContentDto(
                path,
                content,
                content != null ? content.getBytes().length : 0,
                false,
                null
        );
    }

    /**
     * Create a skipped file result (e.g., file too large, binary, or fetch failed).
     */
    public static FileContentDto skipped(String path, String reason) {
        return new FileContentDto(path, null, 0, true, reason);
    }

    /**
     * Create a skipped file result due to size limit.
     */
    public static FileContentDto skippedDueToSize(String path, long actualSize, long maxSize) {
        return new FileContentDto(
                path,
                null,
                actualSize,
                true,
                String.format("File size %d bytes exceeds limit %d bytes", actualSize, maxSize)
        );
    }
}
