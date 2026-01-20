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
 * - multiBranchEnabled: whether multi-branch context is enabled for PR analysis
 *   When enabled, PRs to non-main branches will include both main and target branch context
 * - branchRetentionDays: how long to keep branch index metadata before auto-cleanup (default: 90 days)
 */
@JsonIgnoreProperties(ignoreUnknown = true)
public record RagConfig(
    @JsonProperty("enabled") boolean enabled,
    @JsonProperty("branch") String branch,
    @JsonProperty("excludePatterns") List<String> excludePatterns,
    @JsonProperty("multiBranchEnabled") Boolean multiBranchEnabled,
    @JsonProperty("branchRetentionDays") Integer branchRetentionDays
) {
    public static final int DEFAULT_BRANCH_RETENTION_DAYS = 90;
    
    public RagConfig() {
        this(false, null, null, false, DEFAULT_BRANCH_RETENTION_DAYS);
    }
    
    public RagConfig(boolean enabled) {
        this(enabled, null, null, false, DEFAULT_BRANCH_RETENTION_DAYS);
    }
    
    public RagConfig(boolean enabled, String branch) {
        this(enabled, branch, null, false, DEFAULT_BRANCH_RETENTION_DAYS);
    }
    
    public RagConfig(boolean enabled, String branch, List<String> excludePatterns) {
        this(enabled, branch, excludePatterns, false, DEFAULT_BRANCH_RETENTION_DAYS);
    }
    
    /**
     * Check if multi-branch context is enabled for PR analysis.
     */
    public boolean isMultiBranchEnabled() {
        return multiBranchEnabled != null && multiBranchEnabled;
    }
    
    /**
     * Get effective branch retention days.
     */
    public int getEffectiveBranchRetentionDays() {
        return branchRetentionDays != null ? branchRetentionDays : DEFAULT_BRANCH_RETENTION_DAYS;
    }
    
    /**
     * Check if a branch should have indexed context based on branchPushPatterns.
     * @param branchName the branch to check
     * @param branchPushPatterns patterns from BranchAnalysisConfig
     * @return true if branch matches any pattern and multi-branch is enabled
     */
    public boolean shouldHaveBranchIndex(String branchName, List<String> branchPushPatterns) {
        if (!isMultiBranchEnabled() || branchPushPatterns == null || branchPushPatterns.isEmpty()) {
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
