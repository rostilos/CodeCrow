package org.rostilos.codecrow.events.analysis;

import org.rostilos.codecrow.events.CodecrowEvent;

import java.time.Duration;
import java.util.Map;

/**
 * Event fired when a code analysis is completed.
 */
public class AnalysisCompletedEvent extends CodecrowEvent {
    
    public enum CompletionStatus {
        SUCCESS,
        PARTIAL_SUCCESS,
        FAILED,
        CANCELLED
    }
    
    private final Long projectId;
    private final Long jobId;
    private final CompletionStatus status;
    private final Duration duration;
    private final int issuesFound;
    private final int filesAnalyzed;
    private final String errorMessage;
    private final Map<String, Object> metrics;
    
    public AnalysisCompletedEvent(Object source, String correlationId, Long projectId, 
                                   Long jobId, CompletionStatus status, Duration duration,
                                   int issuesFound, int filesAnalyzed, String errorMessage,
                                   Map<String, Object> metrics) {
        super(source, correlationId);
        this.projectId = projectId;
        this.jobId = jobId;
        this.status = status;
        this.duration = duration;
        this.issuesFound = issuesFound;
        this.filesAnalyzed = filesAnalyzed;
        this.errorMessage = errorMessage;
        this.metrics = metrics;
    }
    
    @Override
    public String getEventType() {
        return "ANALYSIS_COMPLETED";
    }
    
    public Long getProjectId() {
        return projectId;
    }
    
    public Long getJobId() {
        return jobId;
    }
    
    public CompletionStatus getStatus() {
        return status;
    }
    
    public Duration getDuration() {
        return duration;
    }
    
    public int getIssuesFound() {
        return issuesFound;
    }
    
    public int getFilesAnalyzed() {
        return filesAnalyzed;
    }
    
    public String getErrorMessage() {
        return errorMessage;
    }
    
    public Map<String, Object> getMetrics() {
        return metrics;
    }
    
    public boolean isSuccessful() {
        return status == CompletionStatus.SUCCESS || status == CompletionStatus.PARTIAL_SUCCESS;
    }
}
