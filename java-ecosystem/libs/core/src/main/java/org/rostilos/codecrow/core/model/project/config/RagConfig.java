package org.rostilos.codecrow.core.model.project.config;

import com.fasterxml.jackson.annotation.JsonIgnoreProperties;
import com.fasterxml.jackson.annotation.JsonProperty;

import java.util.List;

/**
 * Configuration for RAG (Retrieval-Augmented Generation) indexing.
 * - enabled: whether RAG indexing is enabled for this project
 * - branch: the base branch to index (if null, uses the configured project or
 * repository default branch; indexing fails explicitly when none is available)
 * - includePatterns: list of glob patterns for paths to include in indexing (applied first)
 *   When non-empty, only files matching at least one pattern are considered.
 * - excludePatterns: list of glob patterns for paths to exclude from indexing (applied after include)
 *   Supports exact paths (e.g., "vendor/") and glob patterns (e.g., "app/code/**", "*.generated.ts")
 * Branch ownership follows the project's branch-analysis target/push patterns.
 * Retention is an operator deployment concern rather than a per-project policy.
 */
@JsonIgnoreProperties(ignoreUnknown = true)
public record RagConfig(
    @JsonProperty("enabled") boolean enabled,
    @JsonProperty("branch") String branch,
    @JsonProperty("includePatterns") List<String> includePatterns,
    @JsonProperty("excludePatterns") List<String> excludePatterns
) {
    public RagConfig() {
        this(false, null, null, null);
    }
    
    public RagConfig(boolean enabled) {
        this(enabled, null, null, null);
    }
    
    public RagConfig(boolean enabled, String branch) {
        this(enabled, branch, null, null);
    }
    
    public RagConfig(boolean enabled, String branch, List<String> excludePatterns) {
        this(enabled, branch, null, excludePatterns);
    }
}
