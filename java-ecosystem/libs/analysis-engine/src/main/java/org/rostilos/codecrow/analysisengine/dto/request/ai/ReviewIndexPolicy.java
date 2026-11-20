package org.rostilos.codecrow.analysisengine.dto.request.ai;

import com.fasterxml.jackson.annotation.JsonProperty;
import java.util.List;

/** Repository selection is independent of whether a reusable graph seed exists. */
public record ReviewIndexPolicy(
        @JsonProperty("include_patterns") List<String> includePatterns,
        @JsonProperty("exclude_patterns") List<String> excludePatterns,
        @JsonProperty("project_type") String projectType,
        @JsonProperty("source_root") String sourceRoot) {
    public ReviewIndexPolicy {
        includePatterns = includePatterns == null ? List.of() : includePatterns.stream().filter(java.util.Objects::nonNull).toList();
        excludePatterns = excludePatterns == null ? List.of() : excludePatterns.stream().filter(java.util.Objects::nonNull).toList();
    }
}
