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
 */
public record ProjectConfig(
    boolean useLocalMcp,
    String defaultBranch,
    BranchAnalysisConfig branchAnalysis
) {
    public ProjectConfig() {
        this(false, null, null);
    }
    
    public ProjectConfig(boolean useLocalMcp, String defaultBranch) {
        this(useLocalMcp, defaultBranch, null);
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
}
