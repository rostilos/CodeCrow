package org.rostilos.codecrow.mcp.bitbucket.cloud.model;

import com.fasterxml.jackson.annotation.JsonIgnoreProperties;
import com.fasterxml.jackson.annotation.JsonProperty;
import com.fasterxml.jackson.databind.JsonNode;
import java.util.List;
import java.util.Map;

@JsonIgnoreProperties(ignoreUnknown = true)
public record BitbucketBranchingModel(
        String type,
        DevelopmentDto development,
        ProductionDto production,
        @JsonProperty("branch_types") List<BranchTypeDto> branchTypes,
        Map<String, JsonNode> links
) {
    public record DevelopmentDto(
            String name,
            BitbucketBranch branch,
            @JsonProperty("use_mainbranch") boolean useMainbranch
    ) {}

    public record ProductionDto(
            String name,
            BitbucketBranch branch,
            @JsonProperty("use_mainbranch") boolean useMainbranch
    ) {}

    public record BranchTypeDto(
            String kind,
            String prefix
    ) {}
}