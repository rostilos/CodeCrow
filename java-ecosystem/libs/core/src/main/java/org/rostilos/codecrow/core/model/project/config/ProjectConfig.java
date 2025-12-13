package org.rostilos.codecrow.core.model.project.config;

import java.util.List;

/**
 * Project-level configuration stored as JSON in the project.configuration column.
 *
 * Currently supports:
 *  - useLocalMcp: when true, MCP servers should prefer local repository access (LocalRepoClient)
 *    when a local repository path is available (for example when analysis is executed from an uploaded archive).
 *  - defaultBranch: optional default branch name for the project (eg "main" or "master").
 *  - branchAnalysis: configuration for branch-based analysis filtering.
 *  - ragConfig: configuration for RAG (Retrieval-Augmented Generation) indexing.
 *  - prAnalysisEnabled: whether to auto-analyze PRs on creation/updates (default: true).
 *  - branchAnalysisEnabled: whether to analyze branch pushes (default: true).
 *  - installationMethod: how the project integration is installed (WEBHOOK, PIPELINE, GITHUB_ACTION).
 */
public record ProjectConfig(
    boolean useLocalMcp,
    String defaultBranch,
    BranchAnalysisConfig branchAnalysis,
    RagConfig ragConfig,
    Boolean prAnalysisEnabled,
    Boolean branchAnalysisEnabled,
    InstallationMethod installationMethod
) {
    public ProjectConfig() {
        this(false, null, null, null, true, true, null);
    }
    
    public ProjectConfig(boolean useLocalMcp, String defaultBranch) {
        this(useLocalMcp, defaultBranch, null, null, true, true, null);
    }
    
    public ProjectConfig(boolean useLocalMcp, String defaultBranch, BranchAnalysisConfig branchAnalysis) {
        this(useLocalMcp, defaultBranch, branchAnalysis, null, true, true, null);
    }
    
    public ProjectConfig(boolean useLocalMcp, String defaultBranch, BranchAnalysisConfig branchAnalysis, RagConfig ragConfig) {
        this(useLocalMcp, defaultBranch, branchAnalysis, ragConfig, true, true, null);
    }
    
    /**
     * TODO:Check if PR analysis is enabled (defaults to true if null).
     */
    public boolean isPrAnalysisEnabled() {
        return prAnalysisEnabled == null || prAnalysisEnabled;
    }
    
    /**
     * TODO:Check if branch analysis is enabled (defaults to true if null).
     */
    public boolean isBranchAnalysisEnabled() {
        return branchAnalysisEnabled == null || branchAnalysisEnabled;
    }

    public enum InstallationMethod {
        WEBHOOK,
        PIPELINE,
        GITHUB_ACTION
    }
    
    /**
     * Configuration for branch analysis filtering.
     * Supports exact names and glob patterns (e.g., "develop", "feature/*", "release/**").
     */
    public record BranchAnalysisConfig(
        List<String> prTargetBranches,
        List<String> branchPushPatterns
    ) {
        public BranchAnalysisConfig() {
            this(null, null);
        }
    }
    
    /**
     * Configuration for RAG (Retrieval-Augmented Generation) indexing.
     * - enabled: whether RAG indexing is enabled for this project
     * - branch: the branch to index (if null, uses defaultBranch or 'main')
     * - excludePatterns: list of glob patterns for paths to exclude from indexing
     *   Supports exact paths (e.g., "vendor/") and glob patterns (e.g., "app/code/**", "*.generated.ts")
     */
    public record RagConfig(
        boolean enabled,
        String branch,
        List<String> excludePatterns
    ) {
        public RagConfig() {
            this(false, null, null);
        }
        
        public RagConfig(boolean enabled) {
            this(enabled, null, null);
        }
        
        public RagConfig(boolean enabled, String branch) {
            this(enabled, branch, null);
        }
    }
}
