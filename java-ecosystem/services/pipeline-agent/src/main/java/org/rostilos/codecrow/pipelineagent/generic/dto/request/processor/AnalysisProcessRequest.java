package org.rostilos.codecrow.pipelineagent.generic.dto.request.processor;

import org.rostilos.codecrow.core.model.codeanalysis.AnalysisType;

public interface AnalysisProcessRequest {
    Long getProjectId();
    String getCommitHash();
    AnalysisType getAnalysisType();
    String getTargetBranchName();
}
