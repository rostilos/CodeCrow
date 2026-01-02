package org.rostilos.codecrow.mcp.bitbucket.cloud.model;

import com.fasterxml.jackson.annotation.JsonIgnoreProperties;
import com.fasterxml.jackson.annotation.JsonProperty;
import java.util.List;
import java.util.Map;

@JsonIgnoreProperties(ignoreUnknown = true)
public record BitbucketBranchingModelSettings(
        String type,
        DevelopmentDto development,
        ProductionDto production,
        @JsonProperty("branch_types") List<BranchTypeDto> branchTypes,
        Map<String, BitbucketLink[]> links
) {
    public record DevelopmentDto(
            String name,
            @JsonProperty("use_mainbranch") boolean useMainbranch,
            @JsonProperty("is_valid") Boolean isValid
    ) {}

    public record ProductionDto(
            String name,
            @JsonProperty("use_mainbranch") boolean useMainbranch,
            boolean enabled,
            @JsonProperty("is_valid") Boolean isValid
    ) {}

    public record BranchTypeDto(
            String kind,
            String prefix,
            boolean enabled
    ) {}
}