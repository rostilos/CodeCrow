package org.rostilos.codecrow.analysisengine.dto.request.processor;

import org.rostilos.codecrow.core.model.codeanalysis.AnalysisType;

public interface AnalysisProcessRequest {
    Long getProjectId();
    String getCommitHash();
    AnalysisType getAnalysisType();
    String getTargetBranchName();

    /**
     * Durable identity of the accepted analysis attempt. Queue delivery IDs are
     * deliberately separate because they may change when persisted work is resumed.
     */
    default String getAnalysisRunKey() { return null; }
}
