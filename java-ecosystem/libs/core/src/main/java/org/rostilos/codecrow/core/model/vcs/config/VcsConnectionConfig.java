package org.rostilos.codecrow.core.model.vcs.config;

import com.fasterxml.jackson.annotation.JsonSubTypes;
import com.fasterxml.jackson.annotation.JsonTypeInfo;
import org.rostilos.codecrow.core.model.vcs.config.cloud.BitbucketCloudConfig;
import org.rostilos.codecrow.core.model.vcs.config.github.GitHubConfig;

@JsonTypeInfo(
        use = JsonTypeInfo.Id.NAME,
        include = JsonTypeInfo.As.PROPERTY,
        property = "type"
)
@JsonSubTypes({
        @JsonSubTypes.Type(value = BitbucketCloudConfig.class, name = "bitbucket"),
        @JsonSubTypes.Type(value = GitHubConfig.class, name = "github")
})
public sealed interface VcsConnectionConfig permits GitHubConfig, BitbucketCloudConfig {
}
