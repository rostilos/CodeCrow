package org.rostilos.codecrow.webserver.dto.response.project;

import org.rostilos.codecrow.core.model.analysis.RagIndexStatus;
import org.rostilos.codecrow.core.model.analysis.RagIndexingStatus;

import java.time.OffsetDateTime;

public record RagIndexStatusDTO(
        Long projectId,
        RagIndexingStatus status,
        String indexedBranch,
        String indexedCommitHash,
        Integer totalFilesIndexed,
        OffsetDateTime lastIndexedAt,
        String errorMessage,
        String collectionName,
        Integer failedIncrementalCount
) {
    public static RagIndexStatusDTO fromEntity(RagIndexStatus entity) {
        if (entity == null) {
            return null;
        }
        return new RagIndexStatusDTO(
                entity.getProject().getId(),
                entity.getStatus(),
                entity.getIndexedBranch(),
                entity.getIndexedCommitHash(),
                entity.getTotalFilesIndexed(),
                entity.getLastIndexedAt(),
                entity.getErrorMessage(),
                entity.getCollectionName(),
                entity.getFailedIncrementalCount()
        );
    }
}
