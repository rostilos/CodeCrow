package org.rostilos.codecrow.pipelineagent.generic.dto.request.ai;

import org.rostilos.codecrow.core.model.ai.AIProviderKey;
import org.rostilos.codecrow.core.model.codeanalysis.AnalysisType;

public interface AiAnalysisRequest {
    Long getProjectId();
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
    AnalysisType getAnalysisType();
    /**
     * Returns the VCS provider identifier (e.g., "github", "bitbucket_cloud").
     * Used by the MCP server to select the appropriate VCS client implementation.
     */
    String getVcsProvider();
}
