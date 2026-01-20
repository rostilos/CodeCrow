package org.rostilos.codecrow.core.model.project.config;

import com.fasterxml.jackson.annotation.JsonIgnoreProperties;
import com.fasterxml.jackson.annotation.JsonProperty;

import java.util.List;

/**
 * Configuration for branch analysis filtering.
 * Supports exact names and glob patterns (e.g., "develop", "feature/*", "release/**").
 */
@JsonIgnoreProperties(ignoreUnknown = true)
public record BranchAnalysisConfig(
    @JsonProperty("prTargetBranches") List<String> prTargetBranches,
    @JsonProperty("branchPushPatterns") List<String> branchPushPatterns
) {
    public BranchAnalysisConfig() {
        this(null, null);
    }
}
