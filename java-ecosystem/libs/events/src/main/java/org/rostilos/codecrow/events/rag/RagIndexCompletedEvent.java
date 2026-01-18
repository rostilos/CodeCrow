package org.rostilos.codecrow.events.rag;

import org.rostilos.codecrow.events.CodecrowEvent;

import java.time.Duration;

/**
 * Event fired when RAG index creation or update is completed.
 */
public class RagIndexCompletedEvent extends CodecrowEvent {
    
    public enum CompletionStatus {
        SUCCESS,
        FAILED,
        SKIPPED
    }
    
    private final Long projectId;
    private final RagIndexStartedEvent.IndexType indexType;
    private final RagIndexStartedEvent.IndexOperation operation;
    private final CompletionStatus status;
    private final Duration duration;
    private final int chunksCreated;
    private final String errorMessage;
    
    public RagIndexCompletedEvent(Object source, String correlationId, Long projectId,
                                   RagIndexStartedEvent.IndexType indexType,
                                   RagIndexStartedEvent.IndexOperation operation,
                                   CompletionStatus status, Duration duration,
                                   int chunksCreated, String errorMessage) {
        super(source, correlationId);
        this.projectId = projectId;
        this.indexType = indexType;
        this.operation = operation;
        this.status = status;
        this.duration = duration;
        this.chunksCreated = chunksCreated;
        this.errorMessage = errorMessage;
    }
    
    @Override
    public String getEventType() {
        return "RAG_INDEX_COMPLETED";
    }
    
    public Long getProjectId() {
        return projectId;
    }
    
    public RagIndexStartedEvent.IndexType getIndexType() {
        return indexType;
    }
    
    public RagIndexStartedEvent.IndexOperation getOperation() {
        return operation;
    }
    
    public CompletionStatus getStatus() {
        return status;
    }
    
    public Duration getDuration() {
        return duration;
    }
    
    public int getChunksCreated() {
        return chunksCreated;
    }
    
    public String getErrorMessage() {
        return errorMessage;
    }
    
    public boolean isSuccessful() {
        return status == CompletionStatus.SUCCESS;
    }
}
