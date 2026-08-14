package org.rostilos.codecrow.core.model.project.config;

import com.fasterxml.jackson.annotation.JsonIgnore;
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
 * - multiBranchEnabled: whether separately indexed non-main target branches may
 *   be retained. PR retrieval still selects only the immutable VCS target branch;
 *   source changes come from the exact PR overlay rather than a second branch.
 * - branchRetentionDays: how long to keep branch index metadata before auto-cleanup (default: 90 days)
 * - indexedBranches: explicit non-primary branches whose complete snapshots are retained.
 *   Branch analysis patterns do not implicitly retain RAG snapshots.
 * - transientBranchIndexesEnabled: whether an analyzed PR target that is not retained
 *   may receive a revision-pinned temporary snapshot.
 */
@JsonIgnoreProperties(ignoreUnknown = true)
public record RagConfig(
    @JsonProperty("enabled") boolean enabled,
    @JsonProperty("branch") String branch,
    @JsonProperty("includePatterns") List<String> includePatterns,
    @JsonProperty("excludePatterns") List<String> excludePatterns,
    @JsonProperty("multiBranchEnabled") Boolean multiBranchEnabled,
    @JsonProperty("branchRetentionDays") Integer branchRetentionDays,
    @JsonProperty("indexedBranches") List<String> indexedBranches,
    @JsonProperty("transientBranchIndexesEnabled") Boolean transientBranchIndexesEnabled
) {
    public static final int DEFAULT_BRANCH_RETENTION_DAYS = 90;
    
    public RagConfig() {
        this(false, null, null, null, false, DEFAULT_BRANCH_RETENTION_DAYS, null, false);
    }
    
    public RagConfig(boolean enabled) {
        this(enabled, null, null, null, false, DEFAULT_BRANCH_RETENTION_DAYS, null, false);
    }
    
    public RagConfig(boolean enabled, String branch) {
        this(enabled, branch, null, null, false, DEFAULT_BRANCH_RETENTION_DAYS, null, false);
    }
    
    public RagConfig(boolean enabled, String branch, List<String> excludePatterns) {
        this(enabled, branch, null, excludePatterns, false, DEFAULT_BRANCH_RETENTION_DAYS, null, false);
    }
    
    public RagConfig(boolean enabled, String branch, List<String> includePatterns, List<String> excludePatterns) {
        this(enabled, branch, includePatterns, excludePatterns, false, DEFAULT_BRANCH_RETENTION_DAYS, null, false);
    }

    /**
     * Compatibility constructor for configurations written before explicit
     * retained and transient branch ownership was introduced. Such configurations
     * retain no non-primary RAG branches until they are selected explicitly.
     */
    public RagConfig(
            boolean enabled,
            String branch,
            List<String> includePatterns,
            List<String> excludePatterns,
            Boolean multiBranchEnabled,
            Integer branchRetentionDays) {
        this(enabled, branch, includePatterns, excludePatterns, multiBranchEnabled,
                branchRetentionDays, null, false);
    }
    
    /**
     * Check if multi-branch context is enabled for PR analysis.
     */
    @JsonIgnore
    public boolean isMultiBranchEnabled() {
        return multiBranchEnabled != null && multiBranchEnabled;
    }
    
    /**
     * Get effective branch retention days.
     */
    @JsonIgnore
    public int getEffectiveBranchRetentionDays() {
        return branchRetentionDays != null ? branchRetentionDays : DEFAULT_BRANCH_RETENTION_DAYS;
    }

    @JsonIgnore
    public List<String> getEffectiveIndexedBranches() {
        if (indexedBranches == null) {
            return List.of();
        }
        return indexedBranches.stream()
                .filter(value -> value != null && !value.isBlank())
                .map(String::trim)
                .distinct()
                .toList();
    }

    @JsonIgnore
    public boolean isTransientBranchIndexesEnabled() {
        return isMultiBranchEnabled() && Boolean.TRUE.equals(transientBranchIndexesEnabled);
    }
    
    /**
     * Check whether a branch is explicitly configured for a retained RAG index.
     * Branch-analysis patterns intentionally have no effect on this decision.
     *
     * @param branchName the exact branch name to check
     * @return true if the branch is explicitly retained and multi-branch indexing is enabled
     */
    public boolean shouldHaveBranchIndex(String branchName) {
        if (!isMultiBranchEnabled() || branchName == null || branchName.isBlank()) {
            return false;
        }
        return getEffectiveIndexedBranches().contains(branchName.trim());
    }
}
