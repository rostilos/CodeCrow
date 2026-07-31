package org.rostilos.codecrow.webserver.integration.dto.response;

import org.rostilos.codecrow.core.model.vcs.EVcsConnectionType;
import org.rostilos.codecrow.core.model.vcs.EVcsProvider;
import org.rostilos.codecrow.core.model.vcs.EVcsSetupStatus;
import org.rostilos.codecrow.core.model.vcs.VcsConnection;
import org.rostilos.codecrow.core.model.vcs.config.gitlab.GitLabConfig;

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
    String baseUrl,
    boolean installationRequestPending,
    int repoCount,
    LocalDateTime tokenExpiresAt,
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
            gitLabBaseUrl(entity),
            entity.getGithubInstallationRequestId() != null,
            entity.getRepoCount(),
            entity.getTokenExpiresAt(),
            entity.getCreatedAt(),
            entity.getUpdatedAt()
        );
    }

    private static String gitLabBaseUrl(VcsConnection entity) {
        if (entity.getProviderType() != EVcsProvider.GITLAB) {
            return null;
        }
        return entity.getConfiguration() instanceof GitLabConfig config
                ? config.effectiveBaseUrl()
                : GitLabConfig.DEFAULT_BASE_URL;
    }
}
