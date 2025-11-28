package org.rostilos.codecrow.mcp.bitbucket.cloud.model;

import org.rostilos.codecrow.mcp.bitbucket.cloud.model.BitbucketPullRequest.BitbucketCommit;

import com.fasterxml.jackson.annotation.JsonIgnoreProperties;

@JsonIgnoreProperties(ignoreUnknown = true)
public class BitbucketBranch {
    public String name;
    public String type;
    public BitbucketCommit target;
}
