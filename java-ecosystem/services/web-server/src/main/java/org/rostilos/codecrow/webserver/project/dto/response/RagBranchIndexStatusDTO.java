package org.rostilos.codecrow.webserver.project.dto.response;

import java.time.OffsetDateTime;

/**
 * Observable state of one configured RAG branch. Physical collection names are
 * deliberately not exposed: they are internal, tenant-scoped storage details.
 */
public record RagBranchIndexStatusDTO(
        String branchName,
        String role,
        String status,
        String activeRevision,
        String requestedRevision,
        Integer fileCount,
        Integer chunkCount,
        OffsetDateTime lastUpdatedAt,
        String errorMessage) {
}
