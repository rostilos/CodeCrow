package org.rostilos.codecrow.core.model.vcs.config.github;

import com.fasterxml.jackson.annotation.JsonTypeName;
import org.rostilos.codecrow.core.model.vcs.config.VcsConnectionConfig;

import java.util.List;

@JsonTypeName("github")
public record GitHubConfig(
        String accessToken,
        String organizationId,
        List<String> allowedRepos
) implements VcsConnectionConfig {}
