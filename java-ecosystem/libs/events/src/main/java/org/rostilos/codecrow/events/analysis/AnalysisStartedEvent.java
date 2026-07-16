package org.rostilos.codecrow.events.analysis;

import org.rostilos.codecrow.events.CodecrowEvent;

import java.util.regex.Pattern;

/**
 * Event fired when a code analysis is started.
 */
public class AnalysisStartedEvent extends CodecrowEvent {
    private static final Pattern SHA_256 = Pattern.compile("[0-9a-f]{64}");
    
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
    private final String executionId;
    private final String artifactManifestDigest;
    
    public AnalysisStartedEvent(Object source, Long projectId, String projectName, 
                                AnalysisType analysisType, String targetRef, Long jobId) {
        this(source, null, projectId, projectName, analysisType, targetRef, jobId);
    }
    
    public AnalysisStartedEvent(Object source, String correlationId, Long projectId, 
                                String projectName, AnalysisType analysisType, 
                                String targetRef, Long jobId) {
        this(source, correlationId, projectId, projectName, analysisType, targetRef, jobId,
                null, null);
    }

    /**
     * Creates an execution-bound event for the immutable review path. Legacy
     * callers continue to use the constructors above and therefore retain an
     * explicit unbound shape.
     */
    public AnalysisStartedEvent(Object source, String correlationId, Long projectId,
                                String projectName, AnalysisType analysisType,
                                String targetRef, Long jobId, String executionId,
                                String artifactManifestDigest) {
        super(source, correlationId);
        validateExecutionBinding(executionId, artifactManifestDigest);
        this.projectId = projectId;
        this.projectName = projectName;
        this.analysisType = analysisType;
        this.targetRef = targetRef;
        this.jobId = jobId;
        this.executionId = executionId;
        this.artifactManifestDigest = artifactManifestDigest;
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

    public String getExecutionId() {
        return executionId;
    }

    public String getArtifactManifestDigest() {
        return artifactManifestDigest;
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
