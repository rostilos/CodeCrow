package org.rostilos.codecrow.core.dto.gitlab;

import org.rostilos.codecrow.core.model.vcs.EVcsConnectionType;
import org.rostilos.codecrow.core.model.vcs.EVcsProvider;
import org.rostilos.codecrow.core.model.vcs.EVcsSetupStatus;
import org.rostilos.codecrow.core.model.vcs.VcsConnection;
import org.rostilos.codecrow.core.model.vcs.config.gitlab.GitLabConfig;

import java.time.LocalDateTime;

public record GitLabDTO(
        Long id,
        String connectionName,
        String groupId,
        int repoCount,
        EVcsSetupStatus setupStatus,
        Boolean hasAccessToken,
        LocalDateTime updatedAt,
        EVcsConnectionType connectionType,
        String repositoryPath
) {
    public static GitLabDTO fromVcsConnection(VcsConnection vcsConnection) {
        if (vcsConnection.getProviderType() != EVcsProvider.GITLAB) {
            throw new IllegalArgumentException("Expected GitLab connection");
        }
        
        if (vcsConnection.getConnectionType() == EVcsConnectionType.APP || vcsConnection.getConfiguration() == null) {
            return new GitLabDTO(
                    vcsConnection.getId(),
                    vcsConnection.getConnectionName(),
                    vcsConnection.getExternalWorkspaceSlug(),
                    vcsConnection.getRepoCount(),
                    vcsConnection.getSetupStatus(),
                    vcsConnection.getAccessToken() != null && !vcsConnection.getAccessToken().isBlank(),
                    vcsConnection.getUpdatedAt(),
                    vcsConnection.getConnectionType(),
                    vcsConnection.getRepositoryPath()
            );
        }
        
        GitLabConfig config = (GitLabConfig) vcsConnection.getConfiguration();
        return new GitLabDTO(
                vcsConnection.getId(),
                vcsConnection.getConnectionName(),
                config.groupId(),
                vcsConnection.getRepoCount(),
                vcsConnection.getSetupStatus(),
                config.accessToken() != null && !config.accessToken().isBlank(),
                vcsConnection.getUpdatedAt(),
                vcsConnection.getConnectionType(),
                vcsConnection.getRepositoryPath()
        );
    }
}
