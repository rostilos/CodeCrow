package org.rostilos.codecrow.mcp.bitbucket.cloud.model;

import com.fasterxml.jackson.annotation.JsonIgnoreProperties;
import java.util.Map;

@JsonIgnoreProperties(ignoreUnknown = true)
public record BitbucketBranchReference(
        BranchDto branch,
        CommitDto commit,
        BitbucketRepository repository
) {
    @JsonIgnoreProperties(ignoreUnknown = true)
    public record BranchDto(
            String name,
            Map<String, BitbucketLink> links
    ) {}

    @JsonIgnoreProperties(ignoreUnknown = true)
    public record CommitDto(
            String hash,
            Map<String, BitbucketLink> links
    ) {}
}