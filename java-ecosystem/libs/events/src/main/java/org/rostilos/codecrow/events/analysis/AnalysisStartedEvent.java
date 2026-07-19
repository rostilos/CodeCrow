package org.rostilos.codecrow.events.analysis;

import org.rostilos.codecrow.events.CodecrowEvent;

/**
 * Event fired when a code analysis is started.
 */
public class AnalysisStartedEvent extends CodecrowEvent {
    
    public enum AnalysisType {
        PULL_REQUEST,
        BRANCH,
        COMMIT,
        FULL_PROJECT
    }
    
    private final Long projectId;
    private final String projectName;
    private final AnalysisType analysisType;
    private final String targetRef;
    private final Long jobId;
    
    public AnalysisStartedEvent(Object source, Long projectId, String projectName, 
                                AnalysisType analysisType, String targetRef, Long jobId) {
        this(source, null, projectId, projectName, analysisType, targetRef, jobId);
    }
    
    public AnalysisStartedEvent(Object source, String correlationId, Long projectId, 
                                String projectName, AnalysisType analysisType, 
                                String targetRef, Long jobId) {
        super(source, correlationId);
        this.projectId = projectId;
        this.projectName = projectName;
        this.analysisType = analysisType;
        this.targetRef = targetRef;
        this.jobId = jobId;
    }
    
    @Override
    public String getEventType() {
        return "ANALYSIS_STARTED";
    }
    
    public Long getProjectId() {
        return projectId;
    }
    
    public String getProjectName() {
        return projectName;
    }
    
    public AnalysisType getAnalysisType() {
        return analysisType;
    }
    
    public String getTargetRef() {
        return targetRef;
    }
    
    public Long getJobId() {
        return jobId;
    }
}
