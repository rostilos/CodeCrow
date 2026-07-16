package org.rostilos.codecrow.analysisengine.dto.request.ai;

import org.rostilos.codecrow.core.model.ai.AIProviderKey;
import org.rostilos.codecrow.core.model.codeanalysis.AnalysisMode;
import org.rostilos.codecrow.core.model.codeanalysis.AnalysisType;
import java.util.List;
import java.util.Map;

public interface AiAnalysisRequest {
    Long getProjectId();

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

    AnalysisType getAnalysisType();

    String getVcsProvider();

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

    List<String> getDiffSnippets();

    default String getTargetBranchName() { return null; }

    String getRawDiff();

    AnalysisMode getAnalysisMode();

    String getDeltaDiff();

    String getPreviousCommitHash();

    String getCurrentCommitHash();

    /**
     * Exact immutable pull-request base selected during candidate acquisition.
     * Legacy and non-PR requests do not manufacture this coordinate.
     */
    default String getBaseSha() { return null; }

    /**
     * Exact immutable pull-request head selected during candidate acquisition.
     * This is intentionally distinct from incremental-analysis aliases.
     */
    default String getHeadSha() { return null; }

    /** Exact merge base reported by the provider for the selected snapshot. */
    default String getMergeBaseSha() { return null; }

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

    /**
     * The source branch name of the PR (the feature branch it comes FROM).
     * E.g., "feature/my-change".
     */
    default String getSourceBranchName() { return null; }
}
