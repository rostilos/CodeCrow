package org.rostilos.codecrow.core.model.project.config;

import com.fasterxml.jackson.annotation.JsonIgnoreProperties;
import com.fasterxml.jackson.annotation.JsonProperty;
import com.fasterxml.jackson.annotation.JsonSetter;

import java.util.List;
import java.util.Objects;

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
 *  - commentCommands: configuration for PR comment-triggered commands (/codecrow analyze, summarize, ask).
 */
@JsonIgnoreProperties(ignoreUnknown = true)
public class ProjectConfig {
    @JsonProperty("useLocalMcp")
    private boolean useLocalMcp;
    @JsonProperty("defaultBranch")
    private String defaultBranch;
    @JsonProperty("branchAnalysis")
    private BranchAnalysisConfig branchAnalysis;
    @JsonProperty("ragConfig")
    private RagConfig ragConfig;
    @JsonProperty("prAnalysisEnabled")
    private Boolean prAnalysisEnabled;
    @JsonProperty("branchAnalysisEnabled")
    private Boolean branchAnalysisEnabled;
    @JsonProperty("installationMethod")
    private InstallationMethod installationMethod;
    @JsonProperty("commentCommands")
    private CommentCommandsConfig commentCommands;
    
    public ProjectConfig() {
        this.useLocalMcp = false;
        this.prAnalysisEnabled = true;
        this.branchAnalysisEnabled = true;
    }
    
    public ProjectConfig(boolean useLocalMcp, String defaultBranch, BranchAnalysisConfig branchAnalysis,
                         RagConfig ragConfig, Boolean prAnalysisEnabled, Boolean branchAnalysisEnabled,
                         InstallationMethod installationMethod, CommentCommandsConfig commentCommands) {
        this.useLocalMcp = useLocalMcp;
        this.defaultBranch = defaultBranch;
        this.branchAnalysis = branchAnalysis;
        this.ragConfig = ragConfig;
        this.prAnalysisEnabled = prAnalysisEnabled;
        this.branchAnalysisEnabled = branchAnalysisEnabled;
        this.installationMethod = installationMethod;
        this.commentCommands = commentCommands;
    }
    
    public ProjectConfig(boolean useLocalMcp, String defaultBranch) {
        this(useLocalMcp, defaultBranch, null, null, true, true, null, null);
    }
    
    public ProjectConfig(boolean useLocalMcp, String defaultBranch, BranchAnalysisConfig branchAnalysis) {
        this(useLocalMcp, defaultBranch, branchAnalysis, null, true, true, null, null);
    }
    
    public ProjectConfig(boolean useLocalMcp, String defaultBranch, BranchAnalysisConfig branchAnalysis, RagConfig ragConfig) {
        this(useLocalMcp, defaultBranch, branchAnalysis, ragConfig, true, true, null, null);
    }
    
    // Getters
    public boolean useLocalMcp() { return useLocalMcp; }
    public String defaultBranch() { return defaultBranch; }
    public BranchAnalysisConfig branchAnalysis() { return branchAnalysis; }
    public RagConfig ragConfig() { return ragConfig; }
    public Boolean prAnalysisEnabled() { return prAnalysisEnabled; }
    public Boolean branchAnalysisEnabled() { return branchAnalysisEnabled; }
    public InstallationMethod installationMethod() { return installationMethod; }
    public CommentCommandsConfig commentCommands() { return commentCommands; }
    
    // Setters for Jackson
    public void setUseLocalMcp(boolean useLocalMcp) { this.useLocalMcp = useLocalMcp; }
    public void setDefaultBranch(String defaultBranch) { this.defaultBranch = defaultBranch; }
    public void setBranchAnalysis(BranchAnalysisConfig branchAnalysis) { this.branchAnalysis = branchAnalysis; }
    public void setRagConfig(RagConfig ragConfig) { this.ragConfig = ragConfig; }
    public void setPrAnalysisEnabled(Boolean prAnalysisEnabled) { this.prAnalysisEnabled = prAnalysisEnabled; }
    public void setBranchAnalysisEnabled(Boolean branchAnalysisEnabled) { this.branchAnalysisEnabled = branchAnalysisEnabled; }
    public void setInstallationMethod(InstallationMethod installationMethod) { this.installationMethod = installationMethod; }
    public void setCommentCommands(CommentCommandsConfig commentCommands) { this.commentCommands = commentCommands; }
    
    /**
     * Handle legacy field name from database.
     */
    @JsonSetter("commentCommandsConfig")
    public void setCommentCommandsConfig(CommentCommandsConfig commentCommands) { 
        this.commentCommands = commentCommands; 
    }
    
    /**
     * Check if PR analysis is enabled (defaults to true if null).
     */
    public boolean isPrAnalysisEnabled() {
        return prAnalysisEnabled == null || prAnalysisEnabled;
    }
    
    /**
     * Check if branch analysis is enabled (defaults to true if null).
     */
    public boolean isBranchAnalysisEnabled() {
        return branchAnalysisEnabled == null || branchAnalysisEnabled;
    }
    
    /**
     * Check if comment commands are enabled for this project.
     */
    public boolean isCommentCommandsEnabled() {
        return commentCommands != null && commentCommands.enabled();
    }
    
    /**
     * Get the comment commands configuration, or a default disabled config if null.
     */
    public CommentCommandsConfig getCommentCommandsConfig() {
        return commentCommands != null ? commentCommands : new CommentCommandsConfig();
    }
    
    @Override
    public boolean equals(Object o) {
        if (this == o) return true;
        if (o == null || getClass() != o.getClass()) return false;
        ProjectConfig that = (ProjectConfig) o;
        return useLocalMcp == that.useLocalMcp &&
               Objects.equals(defaultBranch, that.defaultBranch) &&
               Objects.equals(branchAnalysis, that.branchAnalysis) &&
               Objects.equals(ragConfig, that.ragConfig) &&
               Objects.equals(prAnalysisEnabled, that.prAnalysisEnabled) &&
               Objects.equals(branchAnalysisEnabled, that.branchAnalysisEnabled) &&
               installationMethod == that.installationMethod &&
               Objects.equals(commentCommands, that.commentCommands);
    }
    
    @Override
    public int hashCode() {
        return Objects.hash(useLocalMcp, defaultBranch, branchAnalysis, ragConfig, 
                           prAnalysisEnabled, branchAnalysisEnabled, installationMethod, commentCommands);
    }
    
    @Override
    public String toString() {
        return "ProjectConfig{" +
               "useLocalMcp=" + useLocalMcp +
               ", defaultBranch='" + defaultBranch + '\'' +
               ", branchAnalysis=" + branchAnalysis +
               ", ragConfig=" + ragConfig +
               ", prAnalysisEnabled=" + prAnalysisEnabled +
               ", branchAnalysisEnabled=" + branchAnalysisEnabled +
               ", installationMethod=" + installationMethod +
               ", commentCommands=" + commentCommands +
               '}';
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
    @JsonIgnoreProperties(ignoreUnknown = true)
    public record BranchAnalysisConfig(
        @JsonProperty("prTargetBranches") List<String> prTargetBranches,
        @JsonProperty("branchPushPatterns") List<String> branchPushPatterns
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
    @JsonIgnoreProperties(ignoreUnknown = true)
    public record RagConfig(
        @JsonProperty("enabled") boolean enabled,
        @JsonProperty("branch") String branch,
        @JsonProperty("excludePatterns") List<String> excludePatterns
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
    
    /**
     * Configuration for comment-triggered commands (/codecrow analyze, summarize, ask).
     * Only available when project is connected via App integration (Bitbucket App or GitHub App).
     * 
     * @param enabled Whether comment commands are enabled for this project
     * @param rateLimit Maximum number of commands allowed per rate limit window
     * @param rateLimitWindowMinutes Duration of the rate limit window in minutes
     * @param allowPublicRepoCommands Whether to allow commands on public repositories (requires high privilege users)
     * @param allowedCommands List of allowed command types (null = all commands allowed)
     */
    @JsonIgnoreProperties(ignoreUnknown = true)
    public record CommentCommandsConfig(
        @JsonProperty("enabled") boolean enabled,
        @JsonProperty("rateLimit") Integer rateLimit,
        @JsonProperty("rateLimitWindowMinutes") Integer rateLimitWindowMinutes,
        @JsonProperty("allowPublicRepoCommands") Boolean allowPublicRepoCommands,
        @JsonProperty("allowedCommands") List<String> allowedCommands
    ) {
        public static final int DEFAULT_RATE_LIMIT = 10;
        public static final int DEFAULT_RATE_LIMIT_WINDOW_MINUTES = 60;
        
        /**
         * Default constructor - commands are ENABLED by default.
         */
        public CommentCommandsConfig() {
            this(true, DEFAULT_RATE_LIMIT, DEFAULT_RATE_LIMIT_WINDOW_MINUTES, false, null);
        }
        
        public CommentCommandsConfig(boolean enabled) {
            this(enabled, DEFAULT_RATE_LIMIT, DEFAULT_RATE_LIMIT_WINDOW_MINUTES, false, null);
        }
        
        /**
         * Get the effective rate limit (defaults to DEFAULT_RATE_LIMIT if null).
         */
        public int getEffectiveRateLimit() {
            return rateLimit != null ? rateLimit : DEFAULT_RATE_LIMIT;
        }
        
        /**
         * Get the effective rate limit window in minutes (defaults to DEFAULT_RATE_LIMIT_WINDOW_MINUTES if null).
         */
        public int getEffectiveRateLimitWindowMinutes() {
            return rateLimitWindowMinutes != null ? rateLimitWindowMinutes : DEFAULT_RATE_LIMIT_WINDOW_MINUTES;
        }
        
        /**
         * Check if a specific command type is allowed.
         * @param commandType The command type (e.g., "analyze", "summarize", "ask")
         * @return true if the command is allowed (null allowedCommands means all are allowed)
         */
        public boolean isCommandAllowed(String commandType) {
            return allowedCommands == null || allowedCommands.isEmpty() || allowedCommands.contains(commandType);
        }
        
        /**
         * Check if commands are allowed on public repositories.
         */
        public boolean allowsPublicRepoCommands() {
            return allowPublicRepoCommands != null && allowPublicRepoCommands;
        }
    }
}
