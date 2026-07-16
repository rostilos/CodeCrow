package org.rostilos.codecrow.core.model.project.config;

import com.fasterxml.jackson.annotation.JsonIgnoreProperties;

/**
 * Hard, pre-analysis spending limits. Null values inherit from the parent
 * scope (project -> workspace -> deployment defaults).
 */
@JsonIgnoreProperties(ignoreUnknown = true)
public record AnalysisLimitsConfig(
        Integer maxFiles,
        Long maxFileSizeBytes,
        Long maxTotalDiffSizeBytes,
        Integer maxTotalTokens) {

    public AnalysisLimitsConfig {
        requirePositive("maxFiles", maxFiles);
        requirePositive("maxFileSizeBytes", maxFileSizeBytes);
        requirePositive("maxTotalDiffSizeBytes", maxTotalDiffSizeBytes);
        requirePositive("maxTotalTokens", maxTotalTokens);
    }

    private static void requirePositive(String field, Number value) {
        if (value != null && value.longValue() <= 0) {
            throw new IllegalArgumentException(field + " must be greater than zero");
        }
    }

    public static AnalysisLimitsConfig empty() {
        return new AnalysisLimitsConfig(null, null, null, null);
    }
}
