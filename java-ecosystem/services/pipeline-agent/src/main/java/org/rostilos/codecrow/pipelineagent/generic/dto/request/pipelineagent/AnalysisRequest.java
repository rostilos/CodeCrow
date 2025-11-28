package org.rostilos.codecrow.pipelineagent.generic.dto.request.pipelineagent;

import org.rostilos.codecrow.core.model.codeanalysis.AnalysisType;

public interface AnalysisRequest {
    Long getProjectId();
    String getCommitHash();
    AnalysisType getAnalysisType();
    String getTargetBranchName();
}
