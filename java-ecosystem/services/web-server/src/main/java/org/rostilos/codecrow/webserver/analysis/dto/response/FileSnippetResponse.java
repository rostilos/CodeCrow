package org.rostilos.codecrow.webserver.analysis.dto.response;

import java.util.List;

/**
 * Response DTO for the file snippet endpoint.
 * Returns a window of source code lines around a specific issue with inline annotations.
 * Used for the inline code preview on issue detail pages.
 */
public record FileSnippetResponse(
        /** The repo-relative file path. */
        String filePath,
        /** Analysis ID this snippet belongs to. */
        Long analysisId,
        /** Starting line number of the snippet (1-based, inclusive). */
        int startLine,
        /** Ending line number of the snippet (1-based, inclusive). */
        int endLine,
        /** Total line count of the full file. */
        int totalLineCount,
        /** The source lines in this snippet window. */
        List<SnippetLine> lines,
        /** Issues in this snippet window, positioned by line number. */
        List<FileViewResponse.InlineIssue> issues
) {
    /**
     * A single source code line with its line number and content.
     */
    public record SnippetLine(
            int lineNumber,
            String content
    ) {}
}
