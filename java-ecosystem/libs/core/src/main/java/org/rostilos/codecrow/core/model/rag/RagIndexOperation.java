package org.rostilos.codecrow.core.model.rag;

import jakarta.persistence.*;
import org.rostilos.codecrow.core.model.project.Project;

import java.time.OffsetDateTime;

/** Durable, idempotent request to move one branch index to a desired revision. */
@Entity
@Table(name = "rag_index_operation", uniqueConstraints = {
        @UniqueConstraint(name = "uq_rag_index_operation_key", columnNames = {"project_id", "operation_key"})
})
public class RagIndexOperation {

    @Id
    @GeneratedValue(strategy = GenerationType.IDENTITY)
    private Long id;

    @ManyToOne(fetch = FetchType.LAZY, optional = false)
    @JoinColumn(name = "project_id", nullable = false)
    private Project project;

    @Column(name = "branch_name", nullable = false, length = 256)
    private String branchName;

    @Column(name = "from_revision", length = 64)
    private String fromRevision;

    @Column(name = "to_revision", nullable = false, length = 64)
    private String toRevision;

    @Column(name = "operation_key", nullable = false, length = 128)
    private String operationKey;

    @Enumerated(EnumType.STRING)
    @Column(name = "status", nullable = false, length = 24)
    private RagIndexOperationStatus status = RagIndexOperationStatus.PENDING;

    @ManyToOne(fetch = FetchType.LAZY)
    @JoinColumn(name = "generation_id")
    private RagBranchIndexGeneration generation;

    @Column(name = "job_id")
    private Long jobId;

    @Column(name = "attempt_count", nullable = false)
    private int attemptCount;

    @Column(name = "created_at", nullable = false, updatable = false)
    private OffsetDateTime createdAt = OffsetDateTime.now();

    @Column(name = "updated_at", nullable = false)
    private OffsetDateTime updatedAt = OffsetDateTime.now();

    @Column(name = "completed_at")
    private OffsetDateTime completedAt;

    @Column(name = "error_message", columnDefinition = "TEXT")
    private String errorMessage;

    public RagIndexOperation() {
    }

    public RagIndexOperation(
            Project project,
            String branchName,
            String fromRevision,
            String toRevision,
            String operationKey) {
        this.project = project;
        this.branchName = branchName;
        this.fromRevision = fromRevision;
        this.toRevision = toRevision;
        this.operationKey = operationKey;
    }

    @PreUpdate
    void onUpdate() {
        updatedAt = OffsetDateTime.now();
    }

    public void start() {
        status = RagIndexOperationStatus.RUNNING;
        attemptCount++;
        errorMessage = null;
        completedAt = null;
    }

    public void succeed(RagBranchIndexGeneration generation) {
        this.generation = generation;
        status = RagIndexOperationStatus.SUCCEEDED;
        completedAt = OffsetDateTime.now();
        errorMessage = null;
    }

    public void fail(String errorMessage) {
        status = RagIndexOperationStatus.FAILED;
        completedAt = OffsetDateTime.now();
        this.errorMessage = errorMessage;
    }

    public void heartbeat() {
        if (status == RagIndexOperationStatus.PENDING
                || status == RagIndexOperationStatus.RUNNING) {
            updatedAt = OffsetDateTime.now();
        }
    }

    public Long getId() { return id; }
    public void setId(Long id) { this.id = id; }
    public Project getProject() { return project; }
    public void setProject(Project project) { this.project = project; }
    public String getBranchName() { return branchName; }
    public void setBranchName(String branchName) { this.branchName = branchName; }
    public String getFromRevision() { return fromRevision; }
    public void setFromRevision(String fromRevision) { this.fromRevision = fromRevision; }
    public String getToRevision() { return toRevision; }
    public void setToRevision(String toRevision) { this.toRevision = toRevision; }
    public String getOperationKey() { return operationKey; }
    public void setOperationKey(String operationKey) { this.operationKey = operationKey; }
    public RagIndexOperationStatus getStatus() { return status; }
    public void setStatus(RagIndexOperationStatus status) { this.status = status; }
    public RagBranchIndexGeneration getGeneration() { return generation; }
    public void setGeneration(RagBranchIndexGeneration generation) { this.generation = generation; }
    public Long getJobId() { return jobId; }
    public void setJobId(Long jobId) { this.jobId = jobId; }
    public int getAttemptCount() { return attemptCount; }
    public void setAttemptCount(int attemptCount) { this.attemptCount = attemptCount; }
    public OffsetDateTime getCreatedAt() { return createdAt; }
    public void setCreatedAt(OffsetDateTime createdAt) { this.createdAt = createdAt; }
    public OffsetDateTime getUpdatedAt() { return updatedAt; }
    public void setUpdatedAt(OffsetDateTime updatedAt) { this.updatedAt = updatedAt; }
    public OffsetDateTime getCompletedAt() { return completedAt; }
    public void setCompletedAt(OffsetDateTime completedAt) { this.completedAt = completedAt; }
    public String getErrorMessage() { return errorMessage; }
    public void setErrorMessage(String errorMessage) { this.errorMessage = errorMessage; }
}
