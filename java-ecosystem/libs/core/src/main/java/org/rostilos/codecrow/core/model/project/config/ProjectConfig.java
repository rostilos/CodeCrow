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
 *  - mainBranch: the primary branch (master/main) used as base for RAG training and analysis.
 *    IMPORTANT: This is the single source of truth for the project's main branch. It should be set during
 *    project creation and is used for: RAG base index, multi-branch context base, and always included in
 *    analysis patterns (PR targets and branch pushes).
 *  - defaultBranch: (DEPRECATED - use mainBranch) optional default branch name for the project.
 *  - branchAnalysis: configuration for branch-based analysis filtering.
 *  - ragConfig: configuration for RAG (Retrieval-Augmented Generation) indexing.
 *  - prAnalysisEnabled: whether to auto-analyze PRs on creation/updates (default: true).
 *  - branchAnalysisEnabled: whether to analyze branch pushes (default: true).
 *  - installationMethod: how the project integration is installed (WEBHOOK, PIPELINE, GITHUB_ACTION).
 *  - commentCommands: configuration for PR comment-triggered commands (/codecrow analyze, summarize, ask).
 *  - maxAnalysisTokenLimit: maximum allowed tokens for PR analysis (default: 200000).
 *    Analysis will be skipped if the diff exceeds this limit.
 *  - projectRules: custom project-level review rules (enforce/suppress patterns).
 * 
 * @see BranchAnalysisConfig
 * @see RagConfig
 * @see CommentCommandsConfig
 * @see ProjectRulesConfig
 * @see InstallationMethod
 */
@JsonIgnoreProperties(ignoreUnknown = true)
public class ProjectConfig {
    public static final int DEFAULT_MAX_ANALYSIS_TOKEN_LIMIT = 200000;
    
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
    @JsonProperty("maxAnalysisTokenLimit")
    private Integer maxAnalysisTokenLimit;
    @JsonProperty("projectRules")
    private ProjectRulesConfig projectRules;
    
    public ProjectConfig() {
        this.useLocalMcp = false;
        this.prAnalysisEnabled = true;
        this.branchAnalysisEnabled = true;
        this.maxAnalysisTokenLimit = DEFAULT_MAX_ANALYSIS_TOKEN_LIMIT;
    }
    
    public ProjectConfig(boolean useLocalMcp, String mainBranch, BranchAnalysisConfig branchAnalysis,
                         RagConfig ragConfig, Boolean prAnalysisEnabled, Boolean branchAnalysisEnabled,
                         InstallationMethod installationMethod, CommentCommandsConfig commentCommands) {
        this(useLocalMcp, mainBranch, branchAnalysis, ragConfig, prAnalysisEnabled, branchAnalysisEnabled,
             installationMethod, commentCommands, DEFAULT_MAX_ANALYSIS_TOKEN_LIMIT);
    }
    
    public ProjectConfig(boolean useLocalMcp, String mainBranch, BranchAnalysisConfig branchAnalysis,
                         RagConfig ragConfig, Boolean prAnalysisEnabled, Boolean branchAnalysisEnabled,
                         InstallationMethod installationMethod, CommentCommandsConfig commentCommands,
                         Integer maxAnalysisTokenLimit) {
        this.useLocalMcp = useLocalMcp;
        this.mainBranch = mainBranch;
        this.defaultBranch = mainBranch; // Keep in sync for backward compatibility
        this.branchAnalysis = branchAnalysis;
        this.ragConfig = ragConfig;
        this.prAnalysisEnabled = prAnalysisEnabled;
        this.branchAnalysisEnabled = branchAnalysisEnabled;
        this.installationMethod = installationMethod;
        this.commentCommands = commentCommands;
        this.maxAnalysisTokenLimit = maxAnalysisTokenLimit != null ? maxAnalysisTokenLimit : DEFAULT_MAX_ANALYSIS_TOKEN_LIMIT;
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
    public ProjectRulesConfig projectRules() { return projectRules; }
    
    /**
     * Get the maximum token limit for PR analysis.
     * Returns the configured value or the default (200000) if not set.
     */
    public int maxAnalysisTokenLimit() {
        return maxAnalysisTokenLimit != null ? maxAnalysisTokenLimit : DEFAULT_MAX_ANALYSIS_TOKEN_LIMIT;
    }
    
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
                this.ragConfig.includePatterns(),
                this.ragConfig.excludePatterns(),
                this.ragConfig.multiBranchEnabled(),
                this.ragConfig.branchRetentionDays()
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
    public void setMaxAnalysisTokenLimit(Integer maxAnalysisTokenLimit) { 
        this.maxAnalysisTokenLimit = maxAnalysisTokenLimit != null ? maxAnalysisTokenLimit : DEFAULT_MAX_ANALYSIS_TOKEN_LIMIT; 
    }
    public void setProjectRules(ProjectRulesConfig projectRules) { this.projectRules = projectRules; }

    /**
     * Get the project rules configuration, or a default empty config if null.
     */
    public ProjectRulesConfig getProjectRulesConfig() {
        return projectRules != null ? projectRules : new ProjectRulesConfig();
    }

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
               Objects.equals(commentCommands, that.commentCommands) &&
               Objects.equals(maxAnalysisTokenLimit, that.maxAnalysisTokenLimit) &&
               Objects.equals(projectRules, that.projectRules);
    }
    
    @Override
    public int hashCode() {
        return Objects.hash(useLocalMcp, mainBranch, branchAnalysis, ragConfig, 
                           prAnalysisEnabled, branchAnalysisEnabled, installationMethod, 
                           commentCommands, maxAnalysisTokenLimit, projectRules);
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
               ", maxAnalysisTokenLimit=" + maxAnalysisTokenLimit +
               ", projectRules=" + projectRules +
               '}';
    }
}
