package org.rostilos.codecrow.events.analysis;

import org.rostilos.codecrow.events.CodecrowEvent;

import java.time.Duration;
import java.util.Map;
import java.util.regex.Pattern;

/**
 * Event fired when a code analysis is completed.
 */
public class AnalysisCompletedEvent extends CodecrowEvent {
    private static final Pattern SHA_256 = Pattern.compile("[0-9a-f]{64}");
    
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
    
    // First-class PR metadata fields for event listeners (e.g., RAG PR cleanup)
    // that need these without doing DB lookups.
    private final String projectWorkspace;
    private final String projectNamespace;
    private final Long prNumber;
    private final String executionId;
    private final String artifactManifestDigest;
    
    /**
     * @deprecated Use the constructor with projectWorkspace, projectNamespace, and prNumber.
     */
    @Deprecated
    public AnalysisCompletedEvent(Object source, String correlationId, Long projectId, 
                                   Long jobId, CompletionStatus status, Duration duration,
                                   int issuesFound, int filesAnalyzed, String errorMessage,
                                   Map<String, Object> metrics) {
        this(source, correlationId, projectId, jobId, status, duration, issuesFound, filesAnalyzed,
             errorMessage, metrics, null, null, null, null, null);
    }
    
    public AnalysisCompletedEvent(Object source, String correlationId, Long projectId, 
                                   Long jobId, CompletionStatus status, Duration duration,
                                   int issuesFound, int filesAnalyzed, String errorMessage,
                                   Map<String, Object> metrics,
                                   String projectWorkspace, String projectNamespace, Long prNumber) {
        this(source, correlationId, projectId, jobId, status, duration, issuesFound, filesAnalyzed,
                errorMessage, metrics, projectWorkspace, projectNamespace, prNumber, null, null);
    }

    /**
     * Creates an execution-bound completion event for the immutable review
     * path. Existing constructors remain the explicit legacy shape.
     */
    public AnalysisCompletedEvent(Object source, String correlationId, Long projectId,
                                   Long jobId, CompletionStatus status, Duration duration,
                                   int issuesFound, int filesAnalyzed, String errorMessage,
                                   Map<String, Object> metrics,
                                   String projectWorkspace, String projectNamespace, Long prNumber,
                                   String executionId, String artifactManifestDigest) {
        super(source, correlationId);
        validateExecutionBinding(executionId, artifactManifestDigest);
        this.projectId = projectId;
        this.jobId = jobId;
        this.status = status;
        this.duration = duration;
        this.issuesFound = issuesFound;
        this.filesAnalyzed = filesAnalyzed;
        this.errorMessage = errorMessage;
        this.metrics = metrics;
        this.projectWorkspace = projectWorkspace;
        this.projectNamespace = projectNamespace;
        this.prNumber = prNumber;
        this.executionId = executionId;
        this.artifactManifestDigest = artifactManifestDigest;
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
    
    public String getProjectWorkspace() {
        return projectWorkspace;
    }
    
    public String getProjectNamespace() {
        return projectNamespace;
    }
    
    public Long getPrNumber() {
        return prNumber;
    }

    public String getExecutionId() {
        return executionId;
    }

    public String getArtifactManifestDigest() {
        return artifactManifestDigest;
    }
    
    public boolean isSuccessful() {
        return status == CompletionStatus.SUCCESS || status == CompletionStatus.PARTIAL_SUCCESS;
    }

    private static void validateExecutionBinding(
            String executionId,
            String artifactManifestDigest) {
        if (executionId == null && artifactManifestDigest == null) {
            return;
        }
        if (executionId == null || executionId.isBlank()) {
            throw new IllegalArgumentException("executionId must not be blank");
        }
        if (artifactManifestDigest == null
                || !SHA_256.matcher(artifactManifestDigest).matches()) {
            throw new IllegalArgumentException(
                    "artifactManifestDigest must be a lowercase SHA-256 digest");
        }
    }
}
