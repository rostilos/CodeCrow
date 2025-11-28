package org.rostilos.codecrow.mcp.bitbucket.cloud.model;

import java.util.Map;

import com.fasterxml.jackson.annotation.JsonIgnoreProperties;
import com.fasterxml.jackson.annotation.JsonProperty;
import com.fasterxml.jackson.databind.JsonNode;

@JsonIgnoreProperties(ignoreUnknown = true)
public class BitbucketRepository {
    public String type;
    public String uuid;
    public String slug;
    public String name;
    @JsonProperty("full_name")
    public String fullName;
    public String description;
    @JsonProperty("is_private")
    public boolean isPrivate;
    @JsonProperty("created_on")
    public String createdOn;
    @JsonProperty("updated_on")
    public String updatedOn;
    public long size;
    public String language;
    @JsonProperty("has_issues")
    public boolean hasIssues;
    @JsonProperty("has_wiki")
    public boolean hasWiki;
    @JsonProperty("fork_policy")
    public String forkPolicy;
    public BitbucketAccount owner;
    public BitbucketWorkspace workspace;
    public BitbucketProject project;
    public BitbucketBranch mainbranch;
    public String website;
    public String scm;
    public Map<String, JsonNode> links;
}
