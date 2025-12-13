package org.rostilos.codecrow.core.dto.github;

import org.rostilos.codecrow.core.model.vcs.EVcsConnectionType;
import org.rostilos.codecrow.core.model.vcs.EVcsProvider;
import org.rostilos.codecrow.core.model.vcs.EVcsSetupStatus;
import org.rostilos.codecrow.core.model.vcs.VcsConnection;
import org.rostilos.codecrow.core.model.vcs.config.github.GitHubConfig;

import java.time.LocalDateTime;

public record GitHubDTO(
        Long id,
        String connectionName,
        String organizationId,
        int repoCount,
        EVcsSetupStatus setupStatus,
        Boolean hasAccessToken,
        LocalDateTime updatedAt
) {
    public static GitHubDTO fromVcsConnection(VcsConnection vcsConnection) {
        if (vcsConnection.getProviderType() != EVcsProvider.GITHUB) {
            throw new IllegalArgumentException("Expected GitHub connection");
        }
        
        if (vcsConnection.getConnectionType() == EVcsConnectionType.APP || vcsConnection.getConfiguration() == null) {
            return new GitHubDTO(
                    vcsConnection.getId(),
                    vcsConnection.getConnectionName(),
                    vcsConnection.getExternalWorkspaceSlug(),
                    vcsConnection.getRepoCount(),
                    vcsConnection.getSetupStatus(),
                    vcsConnection.getAccessToken() != null && !vcsConnection.getAccessToken().isBlank(),
                    vcsConnection.getUpdatedAt()
            );
        }
        
        GitHubConfig config = (GitHubConfig) vcsConnection.getConfiguration();
        return new GitHubDTO(
                vcsConnection.getId(),
                vcsConnection.getConnectionName(),
                config.organizationId(),
                vcsConnection.getRepoCount(),
                vcsConnection.getSetupStatus(),
                config.accessToken() != null && !config.accessToken().isBlank(),
                vcsConnection.getUpdatedAt()
        );
    }
}
