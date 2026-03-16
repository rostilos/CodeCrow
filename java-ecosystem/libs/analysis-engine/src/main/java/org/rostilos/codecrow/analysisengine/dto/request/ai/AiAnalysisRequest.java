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
