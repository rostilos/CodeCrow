package org.rostilos.codecrow.core.model.vcs.config.cloud;

import com.fasterxml.jackson.annotation.JsonTypeName;
import org.rostilos.codecrow.core.model.vcs.config.VcsConnectionConfig;

@JsonTypeName("bitbucket")
public record BitbucketCloudConfig(
        String oAuthKey,
        String oAuthToken,
        String workspaceId
) implements VcsConnectionConfig {}
