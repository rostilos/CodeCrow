package org.rostilos.codecrow.core.dto.bitbucket;

import org.rostilos.codecrow.core.model.vcs.config.cloud.BitbucketCloudConfig;
import org.rostilos.codecrow.core.model.vcs.EVcsConnectionType;
import org.rostilos.codecrow.core.model.vcs.EVcsProvider;
import org.rostilos.codecrow.core.model.vcs.EVcsSetupStatus;
import org.rostilos.codecrow.core.model.vcs.VcsConnection;

import java.time.LocalDateTime;

public record BitbucketCloudDTO(
        Long id,
        String connectionName,
        String workspaceId,
        int repoCount,
        EVcsSetupStatus setupStatus,
        Boolean hasAuthKey,
        Boolean hasAuthSecret,
        LocalDateTime updatedAt
) {
    public static BitbucketCloudDTO fromGitConfiguration(VcsConnection vcsConnection) {
        if (vcsConnection.getProviderType() != EVcsProvider.BITBUCKET_CLOUD) {
            throw new IllegalArgumentException("Expected Bitbucket connection");
        }
        
        // Handle APP-based connections (no configuration object)
        if (vcsConnection.getConnectionType() == EVcsConnectionType.APP || 
            vcsConnection.getConnectionType() == EVcsConnectionType.FORGE_APP || 
            vcsConnection.getConfiguration() == null) {
            return new BitbucketCloudDTO(
                    vcsConnection.getId(),
                    vcsConnection.getConnectionName(),
                    vcsConnection.getExternalWorkspaceSlug(),
                    vcsConnection.getRepoCount(),
                    vcsConnection.getSetupStatus(),
                    vcsConnection.getAccessToken() != null && !vcsConnection.getAccessToken().isBlank(),
                    vcsConnection.getRefreshToken() != null && !vcsConnection.getRefreshToken().isBlank(),
                    vcsConnection.getUpdatedAt()
            );
        }
        
        // Handle legacy manual OAuth connections
        BitbucketCloudConfig config = (BitbucketCloudConfig) vcsConnection.getConfiguration();
        return new BitbucketCloudDTO(
                vcsConnection.getId(),
                vcsConnection.getConnectionName(),
                config.workspaceId(),
                vcsConnection.getRepoCount(),
                vcsConnection.getSetupStatus(),
                !config.oAuthKey().isBlank(),
                !config.oAuthToken().isBlank(),
                vcsConnection.getUpdatedAt()
        );
    }
}
