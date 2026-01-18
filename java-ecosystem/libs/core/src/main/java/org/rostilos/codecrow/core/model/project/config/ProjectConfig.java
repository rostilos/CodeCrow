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
 *  - mainBranch: the primary branch (master/main) used as base for RAG training, delta indexes, and analysis.
 *    IMPORTANT: This is the single source of truth for the project's main branch. It should be set during
 *    project creation and is used for: RAG base index, delta index base comparison, and always included in
 *    analysis patterns (PR targets and branch pushes).
 *  - defaultBranch: (DEPRECATED - use mainBranch) optional default branch name for the project.
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

    @JsonProperty("mainBranch")
    private String mainBranch;
    
    /**
     * @deprecated Use mainBranch instead. Kept for backward compatibility.
     */
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
    
    public ProjectConfig(boolean useLocalMcp, String mainBranch, BranchAnalysisConfig branchAnalysis,
                         RagConfig ragConfig, Boolean prAnalysisEnabled, Boolean branchAnalysisEnabled,
                         InstallationMethod installationMethod, CommentCommandsConfig commentCommands) {
        this.useLocalMcp = useLocalMcp;
        this.mainBranch = mainBranch;
        this.defaultBranch = mainBranch; // Keep in sync for backward compatibility
        this.branchAnalysis = branchAnalysis;
        this.ragConfig = ragConfig;
        this.prAnalysisEnabled = prAnalysisEnabled;
        this.branchAnalysisEnabled = branchAnalysisEnabled;
        this.installationMethod = installationMethod;
        this.commentCommands = commentCommands;
    }
    
    public ProjectConfig(boolean useLocalMcp, String mainBranch) {
        this(useLocalMcp, mainBranch, null, null, true, true, null, null);
    }
    
    public ProjectConfig(boolean useLocalMcp, String mainBranch, BranchAnalysisConfig branchAnalysis) {
        this(useLocalMcp, mainBranch, branchAnalysis, null, true, true, null, null);
    }
    
    public ProjectConfig(boolean useLocalMcp, String mainBranch, BranchAnalysisConfig branchAnalysis, RagConfig ragConfig) {
        this(useLocalMcp, mainBranch, branchAnalysis, ragConfig, true, true, null, null);
    }
    
    public boolean useLocalMcp() { return useLocalMcp; }

    public String mainBranch() { 
        if (mainBranch != null) return mainBranch;
        if (defaultBranch != null) return defaultBranch;
        return "main";
    }
    
    /**
     * @deprecated Use mainBranch() instead.
     */
    @Deprecated
    public String defaultBranch() { 
        return mainBranch != null ? mainBranch : defaultBranch; 
    }
    
    public BranchAnalysisConfig branchAnalysis() { return branchAnalysis; }
    public RagConfig ragConfig() { return ragConfig; }
    public Boolean prAnalysisEnabled() { return prAnalysisEnabled; }
    public Boolean branchAnalysisEnabled() { return branchAnalysisEnabled; }
    public InstallationMethod installationMethod() { return installationMethod; }
    public CommentCommandsConfig commentCommands() { return commentCommands; }
    
    // Setters for Jackson
    public void setUseLocalMcp(boolean useLocalMcp) { this.useLocalMcp = useLocalMcp; }

    public void setMainBranch(String mainBranch) { 
        this.mainBranch = mainBranch;
        this.defaultBranch = mainBranch; // Keep in sync
        
        // Auto-sync RAG config branch when main branch is set
        if (mainBranch != null && this.ragConfig != null && this.ragConfig.enabled()) {
            this.ragConfig = new RagConfig(
                this.ragConfig.enabled(),
                mainBranch, // Use main branch for RAG
                this.ragConfig.excludePatterns(),
                this.ragConfig.deltaEnabled(),
                this.ragConfig.deltaRetentionDays()
            );
        }
    }
    
    /**
     * @deprecated Use setMainBranch() instead.
     */
    @Deprecated
    public void setDefaultBranch(String defaultBranch) { 
        // If mainBranch is not set, treat defaultBranch as mainBranch
        if (this.mainBranch == null) {
            this.mainBranch = defaultBranch;
        }
        this.defaultBranch = defaultBranch; 
    }
    
    public void setBranchAnalysis(BranchAnalysisConfig branchAnalysis) { this.branchAnalysis = branchAnalysis; }
    public void setRagConfig(RagConfig ragConfig) { this.ragConfig = ragConfig; }
    public void setPrAnalysisEnabled(Boolean prAnalysisEnabled) { this.prAnalysisEnabled = prAnalysisEnabled; }
    public void setBranchAnalysisEnabled(Boolean branchAnalysisEnabled) { this.branchAnalysisEnabled = branchAnalysisEnabled; }
    public void setInstallationMethod(InstallationMethod installationMethod) { this.installationMethod = installationMethod; }
    public void setCommentCommands(CommentCommandsConfig commentCommands) { this.commentCommands = commentCommands; }

    public void ensureMainBranchInPatterns() {
        String main = mainBranch();
        if (main == null) return;
        
        if (this.branchAnalysis != null) {
            List<String> prTargets = this.branchAnalysis.prTargetBranches();
            List<String> pushPatterns = this.branchAnalysis.branchPushPatterns();
            
            boolean prNeedsUpdate = prTargets == null || !prTargets.contains(main);
            boolean pushNeedsUpdate = pushPatterns == null || !pushPatterns.contains(main);
            
            if (prNeedsUpdate || pushNeedsUpdate) {
                List<String> newPrTargets = prTargets != null ? new java.util.ArrayList<>(prTargets) : new java.util.ArrayList<>();
                List<String> newPushPatterns = pushPatterns != null ? new java.util.ArrayList<>(pushPatterns) : new java.util.ArrayList<>();
                
                if (!newPrTargets.contains(main)) {
                    newPrTargets.add(0, main); // Add at beginning
                }
                if (!newPushPatterns.contains(main)) {
                    newPushPatterns.add(0, main); // Add at beginning
                }
                
                this.branchAnalysis = new BranchAnalysisConfig(newPrTargets, newPushPatterns);
            }
        } else {
            this.branchAnalysis = new BranchAnalysisConfig(
                java.util.List.of(main),
                java.util.List.of(main)
            );
        }
    }
    
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
               Objects.equals(mainBranch, that.mainBranch) &&
               Objects.equals(branchAnalysis, that.branchAnalysis) &&
               Objects.equals(ragConfig, that.ragConfig) &&
               Objects.equals(prAnalysisEnabled, that.prAnalysisEnabled) &&
               Objects.equals(branchAnalysisEnabled, that.branchAnalysisEnabled) &&
               installationMethod == that.installationMethod &&
               Objects.equals(commentCommands, that.commentCommands);
    }
    
    @Override
    public int hashCode() {
        return Objects.hash(useLocalMcp, mainBranch, branchAnalysis, ragConfig, 
                           prAnalysisEnabled, branchAnalysisEnabled, installationMethod, commentCommands);
    }
    
    @Override
    public String toString() {
        return "ProjectConfig{" +
               "useLocalMcp=" + useLocalMcp +
               ", mainBranch='" + mainBranch + '\'' +
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
    
    /**
     * Authorization mode for command execution.
     * Controls who can execute CodeCrow commands via PR comments.
     */
    public enum CommandAuthorizationMode {
        ANYONE,
        ALLOWED_USERS_ONLY,
        PR_AUTHOR_ONLY
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
     * @param authorizationMode Controls who can execute commands (default: REPO_WRITE_ACCESS)
     * @param allowPrAuthor If true, PR author can always execute commands regardless of mode
     */
    @JsonIgnoreProperties(ignoreUnknown = true)
    public record CommentCommandsConfig(
        @JsonProperty("enabled") boolean enabled,
        @JsonProperty("rateLimit") Integer rateLimit,
        @JsonProperty("rateLimitWindowMinutes") Integer rateLimitWindowMinutes,
        @JsonProperty("allowPublicRepoCommands") Boolean allowPublicRepoCommands,
        @JsonProperty("allowedCommands") List<String> allowedCommands,
        @JsonProperty("authorizationMode") CommandAuthorizationMode authorizationMode,
        @JsonProperty("allowPrAuthor") Boolean allowPrAuthor
    ) {
        public static final int DEFAULT_RATE_LIMIT = 10;
        public static final int DEFAULT_RATE_LIMIT_WINDOW_MINUTES = 60;
        public static final CommandAuthorizationMode DEFAULT_AUTHORIZATION_MODE = CommandAuthorizationMode.ANYONE;
        
        /**
         * Default constructor - commands are ENABLED by default with ANYONE authorization.
         */
        public CommentCommandsConfig() {
            this(true, DEFAULT_RATE_LIMIT, DEFAULT_RATE_LIMIT_WINDOW_MINUTES, false, null, 
                 DEFAULT_AUTHORIZATION_MODE, true);
        }
        
        public CommentCommandsConfig(boolean enabled) {
            this(enabled, DEFAULT_RATE_LIMIT, DEFAULT_RATE_LIMIT_WINDOW_MINUTES, false, null,
                 DEFAULT_AUTHORIZATION_MODE, true);
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
        
        /**
         * Get the effective authorization mode.
         */
        public CommandAuthorizationMode getEffectiveAuthorizationMode() {
            return authorizationMode != null ? authorizationMode : DEFAULT_AUTHORIZATION_MODE;
        }
        
        /**
         * Check if PR author is always allowed to execute commands.
         */
        public boolean isPrAuthorAllowed() {
            return allowPrAuthor == null || allowPrAuthor;
        }
    }
}
