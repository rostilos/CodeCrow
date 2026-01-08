package org.rostilos.codecrow.core.model.job;

import jakarta.persistence.*;
import org.rostilos.codecrow.core.model.codeanalysis.CodeAnalysis;
import org.rostilos.codecrow.core.model.project.Project;
import org.rostilos.codecrow.core.model.user.User;

import java.time.OffsetDateTime;
import java.util.ArrayList;
import java.util.List;
import java.util.UUID;

/**
 * Represents a background job/process in the system.
 * Jobs can be analysis operations, RAG indexing, or other long-running tasks.
 * Each job has associated logs that can be streamed in real-time or viewed later.
 */
@Entity
@Table(name = "job", indexes = {
        @Index(name = "idx_job_project_id", columnList = "project_id"),
        @Index(name = "idx_job_status", columnList = "status"),
        @Index(name = "idx_job_type", columnList = "job_type"),
        @Index(name = "idx_job_created_at", columnList = "created_at"),
        @Index(name = "idx_job_external_id", columnList = "external_id")
})
public class Job {

    @Id
    @GeneratedValue(strategy = GenerationType.IDENTITY)
    @Column(nullable = false, updatable = false)
    private Long id;

    /**
     * UUID for external reference (used in URLs, API responses).
     * This prevents exposing sequential IDs.
     */
    @Column(name = "external_id", nullable = false, unique = true, length = 36)
    private String externalId = UUID.randomUUID().toString();

    @ManyToOne(fetch = FetchType.LAZY)
    @JoinColumn(name = "project_id", nullable = false)
    private Project project;

    /**
     * User who triggered the job (null for webhook-triggered jobs).
     */
    @ManyToOne(fetch = FetchType.LAZY)
    @JoinColumn(name = "triggered_by_user_id")
    private User triggeredBy;

    @Column(name = "job_type", nullable = false, length = 50)
    @Enumerated(EnumType.STRING)
    private JobType jobType;

    @Column(name = "status", nullable = false, length = 20)
    @Enumerated(EnumType.STRING)
    private JobStatus status = JobStatus.PENDING;

    /**
     * Trigger source - how the job was initiated.
     */
    @Column(name = "trigger_source", nullable = false, length = 30)
    @Enumerated(EnumType.STRING)
    private JobTriggerSource triggerSource;

    /**
     * Human-readable title/description of the job.
     */
    @Column(name = "title", length = 255)
    private String title;

    /**
     * Branch name associated with this job (for analysis jobs).
     */
    @Column(name = "branch_name", length = 255)
    private String branchName;

    /**
     * Pull request number (for PR analysis jobs).
     */
    @Column(name = "pr_number")
    private Long prNumber;

    /**
     * Commit hash being analyzed.
     */
    @Column(name = "commit_hash", length = 40)
    private String commitHash;

    /**
     * Reference to the resulting CodeAnalysis (for analysis jobs).
     */
    @OneToOne(fetch = FetchType.LAZY)
    @JoinColumn(name = "code_analysis_id")
    private CodeAnalysis codeAnalysis;

    /**
     * Error message if job failed.
     */
    @Column(name = "error_message", columnDefinition = "TEXT")
    private String errorMessage;

    /**
     * Progress percentage (0-100).
     */
    @Column(name = "progress")
    private Integer progress = 0;

    /**
     * Current step/phase description.
     */
    @Column(name = "current_step", length = 255)
    private String currentStep;

    @Column(name = "created_at", nullable = false, updatable = false)
    private OffsetDateTime createdAt = OffsetDateTime.now();

    @Column(name = "started_at")
    private OffsetDateTime startedAt;

    @Column(name = "completed_at")
    private OffsetDateTime completedAt;

    @Column(name = "updated_at", nullable = false)
    private OffsetDateTime updatedAt = OffsetDateTime.now();

    @OneToMany(mappedBy = "job", cascade = CascadeType.ALL, orphanRemoval = true, fetch = FetchType.LAZY)
    @OrderBy("timestamp ASC")
    private List<JobLog> logs = new ArrayList<>();

    @PreUpdate
    public void onUpdate() {
        this.updatedAt = OffsetDateTime.now();
    }

    // Helper methods
    public void start() {
        this.status = JobStatus.RUNNING;
        this.startedAt = OffsetDateTime.now();
    }

    public void complete() {
        this.status = JobStatus.COMPLETED;
        this.completedAt = OffsetDateTime.now();
        this.progress = 100;
    }

    public void fail(String errorMessage) {
        this.status = JobStatus.FAILED;
        this.completedAt = OffsetDateTime.now();
        this.errorMessage = errorMessage;
    }

    public void cancel() {
        this.status = JobStatus.CANCELLED;
        this.completedAt = OffsetDateTime.now();
    }

    public void skip(String reason) {
        this.status = JobStatus.SKIPPED;
        this.completedAt = OffsetDateTime.now();
        this.errorMessage = reason;
        this.progress = 100;
    }

    public boolean isTerminal() {
        return status == JobStatus.COMPLETED || status == JobStatus.FAILED || status == JobStatus.CANCELLED || status == JobStatus.SKIPPED;
    }

    public JobLog addLog(JobLogLevel level, String message) {
        JobLog log = new JobLog();
        log.setJob(this);
        log.setLevel(level);
        log.setMessage(message);
        this.logs.add(log);
        return log;
    }

    public JobLog addLog(JobLogLevel level, String step, String message) {
        JobLog log = new JobLog();
        log.setJob(this);
        log.setLevel(level);
        log.setStep(step);
        log.setMessage(message);
        this.logs.add(log);
        return log;
    }

    // Getters and Setters
    public Long getId() { return id; }

    public String getExternalId() { return externalId; }
    public void setExternalId(String externalId) { this.externalId = externalId; }

    public Project getProject() { return project; }
    public void setProject(Project project) { this.project = project; }

    public User getTriggeredBy() { return triggeredBy; }
    public void setTriggeredBy(User triggeredBy) { this.triggeredBy = triggeredBy; }

    public JobType getJobType() { return jobType; }
    public void setJobType(JobType jobType) { this.jobType = jobType; }

    public JobStatus getStatus() { return status; }
    public void setStatus(JobStatus status) { this.status = status; }

    public JobTriggerSource getTriggerSource() { return triggerSource; }
    public void setTriggerSource(JobTriggerSource triggerSource) { this.triggerSource = triggerSource; }

    public String getTitle() { return title; }
    public void setTitle(String title) { this.title = title; }

    public String getBranchName() { return branchName; }
    public void setBranchName(String branchName) { this.branchName = branchName; }

    public Long getPrNumber() { return prNumber; }
    public void setPrNumber(Long prNumber) { this.prNumber = prNumber; }

    public String getCommitHash() { return commitHash; }
    public void setCommitHash(String commitHash) { this.commitHash = commitHash; }

    public CodeAnalysis getCodeAnalysis() { return codeAnalysis; }
    public void setCodeAnalysis(CodeAnalysis codeAnalysis) { this.codeAnalysis = codeAnalysis; }

    public String getErrorMessage() { return errorMessage; }
    public void setErrorMessage(String errorMessage) { this.errorMessage = errorMessage; }

    public Integer getProgress() { return progress; }
    public void setProgress(Integer progress) { this.progress = progress; }

    public String getCurrentStep() { return currentStep; }
    public void setCurrentStep(String currentStep) { this.currentStep = currentStep; }

    public OffsetDateTime getCreatedAt() { return createdAt; }

    public OffsetDateTime getStartedAt() { return startedAt; }
    public void setStartedAt(OffsetDateTime startedAt) { this.startedAt = startedAt; }

    public OffsetDateTime getCompletedAt() { return completedAt; }
    public void setCompletedAt(OffsetDateTime completedAt) { this.completedAt = completedAt; }

    public OffsetDateTime getUpdatedAt() { return updatedAt; }

    public List<JobLog> getLogs() { return logs; }
    public void setLogs(List<JobLog> logs) { this.logs = logs; }
}
