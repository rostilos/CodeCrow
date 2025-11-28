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
    int getMaxAllowedTokens();
    boolean getUseLocalMcp();
    AnalysisType getAnalysisType();
}
