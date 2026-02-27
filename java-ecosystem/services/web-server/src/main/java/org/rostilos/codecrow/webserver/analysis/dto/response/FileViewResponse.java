package org.rostilos.codecrow.webserver.analysis.dto.response;

import java.util.List;

/**
 * Response DTO for the source code viewer endpoint.
 * Contains the file content with inline issue annotations for a specific analysis.
 */
public record FileViewResponse(
        /** The repo-relative file path. */
        String filePath,
        /** The raw file content (for syntax highlighting). */
        String content,
        /** Total line count. */
        int lineCount,
        /** The commit hash this content was captured at. */
        String commitHash,
        /** Analysis ID this view belongs to. */
        Long analysisId,
        /** PR version if this is a PR analysis. */
        Integer prVersion,
        /** Issues annotated on this file, sorted by line number. */
        List<InlineIssue> issues
) {
    /**
     * A single issue annotation positioned at a specific line.
     */
    public record InlineIssue(
            Long issueId,
            int lineNumber,
            String severity,
            String title,
            String reason,
            String category,
            boolean resolved,
            String suggestedFixDescription,
            String suggestedFixDiff,
            /** Tracking lineage: which previous issue this was tracked from (null for first iteration). */
            Long trackedFromIssueId,
            /** Tracking confidence: EXACT, SHIFTED, EDITED, WEAK, or null. */
            String trackingConfidence
    ) {}
}
