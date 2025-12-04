package org.rostilos.codecrow.webserver.dto.response.integration;

import org.rostilos.codecrow.core.model.vcs.EVcsConnectionType;
import org.rostilos.codecrow.core.model.vcs.EVcsProvider;
import org.rostilos.codecrow.core.model.vcs.EVcsSetupStatus;
import org.rostilos.codecrow.core.model.vcs.VcsConnection;

import java.time.LocalDateTime;

/**
 * DTO for VCS connection information.
 */
public record VcsConnectionDTO(
    Long id,
    EVcsProvider provider,
    EVcsConnectionType connectionType,
    String connectionName,
    EVcsSetupStatus status,
    String externalWorkspaceId,
    String externalWorkspaceSlug,
    int repoCount,
    LocalDateTime createdAt,
    LocalDateTime updatedAt
) {
    /**
     * Create DTO from entity.
     */
    public static VcsConnectionDTO fromEntity(VcsConnection entity) {
        return new VcsConnectionDTO(
            entity.getId(),
            entity.getProviderType(),
            entity.getConnectionType(),
            entity.getConnectionName(),
            entity.getSetupStatus(),
            entity.getExternalWorkspaceId(),
            entity.getExternalWorkspaceSlug(),
            entity.getRepoCount(),
            entity.getCreatedAt(),
            entity.getUpdatedAt()
        );
    }
}
