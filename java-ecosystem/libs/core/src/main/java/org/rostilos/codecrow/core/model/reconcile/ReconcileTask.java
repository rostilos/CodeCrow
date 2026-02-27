package org.rostilos.codecrow.core.model.reconcile;

import jakarta.persistence.*;
import java.time.OffsetDateTime;
import java.util.UUID;

/**
 * Represents a queued full-reconciliation task.
 * <p>
 * The web-server creates a row with status {@link ReconcileTaskStatus#PENDING}.
 * The pipeline-agent scheduler polls for pending tasks, sets status to
 * {@link ReconcileTaskStatus#IN_PROGRESS}, runs full reconciliation, then
 * marks the task {@link ReconcileTaskStatus#COMPLETED} or
 * {@link ReconcileTaskStatus#FAILED}.
 */
@Entity
@Table(name = "reconcile_task", indexes = {
        @Index(name = "idx_reconcile_task_status", columnList = "status"),
        @Index(name = "idx_reconcile_task_project_branch", columnList = "project_id, branch_name"),
        @Index(name = "idx_reconcile_task_external_id", columnList = "external_id")
})
public class ReconcileTask {

    @Id
    @GeneratedValue(strategy = GenerationType.IDENTITY)
    @Column(nullable = false, updatable = false)
    private Long id;

    /**
     * UUID for external reference (used in API responses).
     */
    @Column(name = "external_id", nullable = false, unique = true, length = 36)
    private String externalId = UUID.randomUUID().toString();

    @Column(name = "project_id", nullable = false)
    private Long projectId;

    @Column(name = "branch_name", nullable = false, length = 255)
    private String branchName;

    @Enumerated(EnumType.STRING)
    @Column(name = "status", nullable = false, length = 20)
    private ReconcileTaskStatus status = ReconcileTaskStatus.PENDING;

    @Column(name = "created_at", nullable = false, updatable = false)
    private OffsetDateTime createdAt = OffsetDateTime.now();

    @Column(name = "started_at")
    private OffsetDateTime startedAt;

    @Column(name = "completed_at")
    private OffsetDateTime completedAt;

    // ── Result fields (populated on completion) ─────────────────────────

    @Column(name = "total_issues")
    private Integer totalIssues;

    @Column(name = "resolved_issues")
    private Integer resolvedIssues;

    @Column(name = "files_checked")
    private Integer filesChecked;

    @Column(name = "result_message", columnDefinition = "TEXT")
    private String resultMessage;

    @Column(name = "error_message", columnDefinition = "TEXT")
    private String errorMessage;

    // ── Lifecycle helpers ────────────────────────────────────────────────

    public void markInProgress() {
        this.status = ReconcileTaskStatus.IN_PROGRESS;
        this.startedAt = OffsetDateTime.now();
    }

    public void markCompleted(int totalIssues, int resolvedIssues, int filesChecked, String message) {
        this.status = ReconcileTaskStatus.COMPLETED;
        this.completedAt = OffsetDateTime.now();
        this.totalIssues = totalIssues;
        this.resolvedIssues = resolvedIssues;
        this.filesChecked = filesChecked;
        this.resultMessage = message;
    }

    public void markFailed(String errorMessage) {
        this.status = ReconcileTaskStatus.FAILED;
        this.completedAt = OffsetDateTime.now();
        this.errorMessage = errorMessage;
    }

    // ── Getters / Setters ───────────────────────────────────────────────

    public Long getId() { return id; }

    public String getExternalId() { return externalId; }
    public void setExternalId(String externalId) { this.externalId = externalId; }

    public Long getProjectId() { return projectId; }
    public void setProjectId(Long projectId) { this.projectId = projectId; }

    public String getBranchName() { return branchName; }
    public void setBranchName(String branchName) { this.branchName = branchName; }

    public ReconcileTaskStatus getStatus() { return status; }
    public void setStatus(ReconcileTaskStatus status) { this.status = status; }

    public OffsetDateTime getCreatedAt() { return createdAt; }

    public OffsetDateTime getStartedAt() { return startedAt; }
    public void setStartedAt(OffsetDateTime startedAt) { this.startedAt = startedAt; }

    public OffsetDateTime getCompletedAt() { return completedAt; }
    public void setCompletedAt(OffsetDateTime completedAt) { this.completedAt = completedAt; }

    public Integer getTotalIssues() { return totalIssues; }
    public void setTotalIssues(Integer totalIssues) { this.totalIssues = totalIssues; }

    public Integer getResolvedIssues() { return resolvedIssues; }
    public void setResolvedIssues(Integer resolvedIssues) { this.resolvedIssues = resolvedIssues; }

    public Integer getFilesChecked() { return filesChecked; }
    public void setFilesChecked(Integer filesChecked) { this.filesChecked = filesChecked; }

    public String getResultMessage() { return resultMessage; }
    public void setResultMessage(String resultMessage) { this.resultMessage = resultMessage; }

    public String getErrorMessage() { return errorMessage; }
    public void setErrorMessage(String errorMessage) { this.errorMessage = errorMessage; }
}
