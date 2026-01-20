package org.rostilos.codecrow.events.rag;

import org.rostilos.codecrow.events.CodecrowEvent;

/**
 * Event fired when RAG index creation or update is started.
 */
public class RagIndexStartedEvent extends CodecrowEvent {
    
    public enum IndexType {
        MAIN,
        DELTA,
        FULL
    }
    
    public enum IndexOperation {
        CREATE,
        UPDATE,
        DELETE
    }
    
    private final Long projectId;
    private final String projectName;
    private final IndexType indexType;
    private final IndexOperation operation;
    private final String branchName;
    private final String commitHash;
    
    public RagIndexStartedEvent(Object source, Long projectId, String projectName,
                                IndexType indexType, IndexOperation operation,
                                String branchName, String commitHash) {
        this(source, null, projectId, projectName, indexType, operation, branchName, commitHash);
    }
    
    public RagIndexStartedEvent(Object source, String correlationId, Long projectId, 
                                String projectName, IndexType indexType, IndexOperation operation,
                                String branchName, String commitHash) {
        super(source, correlationId);
        this.projectId = projectId;
        this.projectName = projectName;
        this.indexType = indexType;
        this.operation = operation;
        this.branchName = branchName;
        this.commitHash = commitHash;
    }
    
    @Override
    public String getEventType() {
        return "RAG_INDEX_STARTED";
    }
    
    public Long getProjectId() {
        return projectId;
    }
    
    public String getProjectName() {
        return projectName;
    }
    
    public IndexType getIndexType() {
        return indexType;
    }
    
    public IndexOperation getOperation() {
        return operation;
    }
    
    public String getBranchName() {
        return branchName;
    }
    
    public String getCommitHash() {
        return commitHash;
    }
}
