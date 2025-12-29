package org.rostilos.codecrow.mcp.bitbucket.cloud.model;

import com.fasterxml.jackson.annotation.JsonIgnoreProperties;
import com.fasterxml.jackson.annotation.JsonProperty;
import com.fasterxml.jackson.databind.JsonNode;
import java.util.Map;

@JsonIgnoreProperties(ignoreUnknown = true)
public record BitbucketRepository(
        String type,
        String uuid,
        String slug,
        String name,
        @JsonProperty("full_name") String fullName,
        String description,
        @JsonProperty("is_private") boolean isPrivate,
        @JsonProperty("created_on") String createdOn,
        @JsonProperty("updated_on") String updatedOn,
        long size,
        String language,
        @JsonProperty("has_issues") boolean hasIssues,
        @JsonProperty("has_wiki") boolean hasWiki,
        @JsonProperty("fork_policy") String forkPolicy,
        BitbucketAccount owner,
        BitbucketWorkspace workspace,
        BitbucketProject project,
        BitbucketBranch mainbranch,
        String website,
        String scm,
        Map<String, JsonNode> links
) {}