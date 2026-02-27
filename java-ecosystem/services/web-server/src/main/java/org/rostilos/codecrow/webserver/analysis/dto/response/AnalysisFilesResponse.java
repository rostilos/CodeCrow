package org.rostilos.codecrow.webserver.analysis.dto.response;

import java.util.List;

/**
 * Response DTO listing all files that have stored content for a given analysis.
 * Used by the frontend to populate the file tree in the source code viewer.
 */
public record AnalysisFilesResponse(
        Long analysisId,
        String commitHash,
        Integer prVersion,
        List<FileEntry> files
) {
    /**
     * Metadata for a single analyzed file.
     */
    public record FileEntry(
            /** Repo-relative file path. */
            String filePath,
            /** Number of lines in the file. */
            int lineCount,
            /** File size in bytes. */
            long sizeBytes,
            /** Number of issues in this file. */
            int issueCount,
            /** Number of high-severity issues. */
            int highCount,
            /** Number of medium-severity issues. */
            int mediumCount
    ) {}
}
