package org.rostilos.codecrow.core.model.project.config;

import com.fasterxml.jackson.annotation.JsonIgnoreProperties;
import com.fasterxml.jackson.annotation.JsonProperty;

import java.util.List;

/**
 * Configuration for RAG (Retrieval-Augmented Generation) indexing.
 * - enabled: whether RAG indexing is enabled for this project
 * - branch: the base branch to index (if null, uses defaultBranch or 'main')
 * - excludePatterns: list of glob patterns for paths to exclude from indexing
 *   Supports exact paths (e.g., "vendor/") and glob patterns (e.g., "app/code/**", "*.generated.ts")
 * - deltaEnabled: whether to create delta indexes for branch-specific context (e.g., release branches)
 *   When enabled, branches matching branchPushPatterns from BranchAnalysisConfig will get delta indexes
 * - deltaRetentionDays: how long to keep delta indexes before auto-cleanup (default: 90 days)
 */
@JsonIgnoreProperties(ignoreUnknown = true)
public record RagConfig(
    @JsonProperty("enabled") boolean enabled,
    @JsonProperty("branch") String branch,
    @JsonProperty("excludePatterns") List<String> excludePatterns,
    @JsonProperty("deltaEnabled") Boolean deltaEnabled,
    @JsonProperty("deltaRetentionDays") Integer deltaRetentionDays
) {
    public static final int DEFAULT_DELTA_RETENTION_DAYS = 90;
    
    public RagConfig() {
        this(false, null, null, false, DEFAULT_DELTA_RETENTION_DAYS);
    }
    
    public RagConfig(boolean enabled) {
        this(enabled, null, null, false, DEFAULT_DELTA_RETENTION_DAYS);
    }
    
    public RagConfig(boolean enabled, String branch) {
        this(enabled, branch, null, false, DEFAULT_DELTA_RETENTION_DAYS);
    }
    
    public RagConfig(boolean enabled, String branch, List<String> excludePatterns) {
        this(enabled, branch, excludePatterns, false, DEFAULT_DELTA_RETENTION_DAYS);
    }
    
    /**
     * Check if delta indexes are enabled.
     */
    public boolean isDeltaEnabled() {
        return deltaEnabled != null && deltaEnabled;
    }
    
    /**
     * Get effective delta retention days.
     */
    public int getEffectiveDeltaRetentionDays() {
        return deltaRetentionDays != null ? deltaRetentionDays : DEFAULT_DELTA_RETENTION_DAYS;
    }
    
    /**
     * Check if a branch should have a delta index based on branchPushPatterns.
     * @param branchName the branch to check
     * @param branchPushPatterns patterns from BranchAnalysisConfig
     * @return true if branch matches any pattern and delta is enabled
     */
    public boolean shouldHaveDeltaIndex(String branchName, List<String> branchPushPatterns) {
        if (!isDeltaEnabled() || branchPushPatterns == null || branchPushPatterns.isEmpty()) {
            return false;
        }
        return branchPushPatterns.stream()
            .anyMatch(pattern -> matchesBranchPattern(branchName, pattern));
    }
    
    /**
     * Match a branch name against a glob pattern.
     */
    public static boolean matchesBranchPattern(String branchName, String pattern) {
        if (pattern == null || branchName == null) return false;
        // Convert glob pattern to regex
        String regex = pattern
            .replace(".", "\\.")
            .replace("**", "§§")  // Temp placeholder for **
            .replace("*", "[^/]*")
            .replace("§§", ".*");
        return branchName.matches(regex);
    }
}
