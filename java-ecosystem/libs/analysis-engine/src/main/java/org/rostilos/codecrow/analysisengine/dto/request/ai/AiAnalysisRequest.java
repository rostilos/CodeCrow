package org.rostilos.codecrow.analysisengine.dto.request.ai;

import org.rostilos.codecrow.core.model.ai.AIProviderKey;
import org.rostilos.codecrow.core.model.codeanalysis.AnalysisMode;
import org.rostilos.codecrow.core.model.codeanalysis.AnalysisType;
import java.util.List;
import java.util.Map;
import org.rostilos.codecrow.plugins.ProjectCapabilities;
import org.rostilos.codecrow.vcsclient.model.VcsPullRequestChangeManifest;

public interface AiAnalysisRequest {
    Long getProjectId();

    /** Server-owned identity of this accepted analysis attempt. */
    default String getAnalysisRunKey() { return null; }

    default String getProjectWorkspace() { return null; }

    default String getProjectNamespace() { return null; }

    String getProjectVcsWorkspace();

    String getProjectVcsRepoSlug();

    AIProviderKey getAiProvider();

    String getAiModel();

    String getAiApiKey();

    /**
    * Custom base URL for OPENAI_COMPATIBLE provider, or Vertex project/location metadata for GOOGLE_VERTEX.
     * Null for standard providers.
     */
    default String getAiBaseUrl() { return null; }

    /**
     * Optional provider-specific JSON parameters for OPENAI_COMPATIBLE endpoints.
     * Null for standard providers or connections without custom tuning.
     */
    default String getAiCustomParameters() { return null; }

    Long getPullRequestId();

    String getOAuthClient();

    String getOAuthSecret();

    String getAccessToken();

    int getMaxAllowedTokens();

    boolean getUseLocalMcp();

    boolean getUseMcpTools();

    default boolean getRagEnabled() { return true; }

    AnalysisType getAnalysisType();

    String getVcsProvider();

    default String getVcsBaseUrl() { return null; }

    String getPrTitle();

    String getPrDescription();

    /**
     * Optional task-management context (for example Jira issue details)
     * resolved before review analysis starts.
     */
    default Map<String, String> getTaskContext() { return null; }

    /**
     * Optional bounded server-side history for prior PRs associated with the
     * same task key. This is already summarized/capped by Java and must not
     * contain raw historical diffs.
     */
    default String getTaskHistoryContext() { return null; }

    List<String> getChangedFiles();

    List<String> getDeletedFiles();

    /** Base-to-current paths for current-head context maintenance. */
    default List<String> getFullPrChangedFiles() { return getChangedFiles(); }

    /** Base-to-current tombstones, including rename source paths. */
    default List<String> getFullPrDeletedFiles() { return getDeletedFiles(); }

    /**
     * Provider-native path inventory and its completeness receipt. A non-null
     * but incomplete value is useful diagnostics, not permission to bind a
     * complete current-head overlay.
     */
    default VcsPullRequestChangeManifest getPullRequestFileManifest() { return null; }

    default boolean getFullPrManifestComplete() {
        VcsPullRequestChangeManifest manifest = getPullRequestFileManifest();
        return manifest != null && manifest.isComplete();
    }

    /** True when the request only refreshes exact current-head context. */
    default boolean getPrContextMaintenanceRequired() { return false; }

    List<String> getDiffSnippets();

    default String getTargetBranchName() { return null; }

    String getRawDiff();

    AnalysisMode getAnalysisMode();

    String getDeltaDiff();

    String getPreviousCommitHash();

    String getCurrentCommitHash();

    default String getBaseCommitHash() { return null; }

    /**
     * Internal digest of the deployed review contract and review-affecting
     * project/model configuration. It is persisted with PR results and used
     * to decide whether an earlier head is a compatible incremental base.
     */
    default String getAnalysisBehaviorDigest() { return null; }

    /**
     * Previous issues supplied to AI for incremental PR tracking or branch
     * reconciliation.
     */
    default List<AiRequestPreviousIssueDTO> getPreviousCodeAnalysisIssues() { return null; }

    /**
     * File contents pre-fetched by Java for MCP-free reconciliation.
     * Map of filePath → full file content. When non-null and non-empty,
     * Python will use these directly instead of spawning an MCP agent to
     * fetch files via VCS tool calls.
     */
    default Map<String, String> getReconciliationFileContents() { return null; }

    default ProjectCapabilities getProjectCapabilities() { return null; }

    default String getProjectRules() { return null; }

    /**
     * The source branch name of the PR (the feature branch it comes FROM).
     * E.g., "feature/my-change".
     */
    default String getSourceBranchName() { return null; }
}
