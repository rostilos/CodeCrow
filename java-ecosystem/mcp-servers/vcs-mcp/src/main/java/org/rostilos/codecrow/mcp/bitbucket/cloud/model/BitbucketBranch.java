package org.rostilos.codecrow.mcp.bitbucket.cloud.model;

import com.fasterxml.jackson.annotation.JsonIgnoreProperties;
import org.rostilos.codecrow.mcp.bitbucket.cloud.model.BitbucketPullRequest.BitbucketCommit;

@JsonIgnoreProperties(ignoreUnknown = true)
public record BitbucketBranch(
        String name,
        String type,
        BitbucketCommit target
) {}