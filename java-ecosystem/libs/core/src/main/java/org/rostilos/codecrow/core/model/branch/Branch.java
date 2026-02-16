package org.rostilos.codecrow.core.model.branch;

import jakarta.persistence.*;
import org.rostilos.codecrow.core.model.codeanalysis.IssueSeverity;
import org.rostilos.codecrow.core.model.project.Project;

import java.time.OffsetDateTime;
import java.util.ArrayList;
import java.util.List;

@Entity
@Table(name = "branch", uniqueConstraints = {
        @UniqueConstraint(name = "uq_branch_project_branch", columnNames = {"project_id", "branch_name"})
})
public class Branch {

    @Id
    @GeneratedValue(strategy = GenerationType.IDENTITY)
    @Column(nullable = false, updatable = false)
    private Long id;

    @ManyToOne(fetch = FetchType.LAZY)
    @JoinColumn(name = "project_id", nullable = false)
    private Project project;

    @Column(name = "branch_name", nullable = false, length = 200)
    private String branchName;

    @Column(name = "commit_hash", length = 40)
    private String commitHash;

    /** Last commit hash that was fully and successfully analyzed (all steps completed). */
    @Column(name = "last_successful_commit_hash", length = 40)
    private String lastSuccessfulCommitHash;

    @Enumerated(EnumType.STRING)
    @Column(name = "health_status", nullable = false, length = 20)
    private BranchHealthStatus healthStatus = BranchHealthStatus.UNKNOWN;

    @Column(name = "consecutive_failures", nullable = false)
    private int consecutiveFailures = 0;

    @Column(name = "last_health_check_at")
    private OffsetDateTime lastHealthCheckAt;

    @Column(name = "total_issues", nullable = false)
    private int totalIssues = 0;

    @Column(name = "high_severity_count", nullable = false)
    private int highSeverityCount = 0;

    @Column(name = "medium_severity_count", nullable = false)
    private int mediumSeverityCount = 0;

    @Column(name = "low_severity_count", nullable = false)
    private int lowSeverityCount = 0;

    @Column(name = "info_severity_count", nullable = false)
    private int infoSeverityCount = 0;

    @Column(name = "resolved_count", nullable = false)
    private int resolvedCount = 0;

    @OneToMany(mappedBy = "branch", cascade = CascadeType.ALL, orphanRemoval = true, fetch = FetchType.LAZY)
    private List<BranchIssue> issues = new ArrayList<>();

    @Column(name = "created_at", nullable = false, updatable = false)
    private OffsetDateTime createdAt = OffsetDateTime.now();

    @Column(name = "updated_at", nullable = false)
    private OffsetDateTime updatedAt = OffsetDateTime.now();

    @PreUpdate
    public void onUpdate() {
        this.updatedAt = OffsetDateTime.now();
    }

    public void updateIssueCounts() {
        this.totalIssues = issues.stream().filter(i -> !i.isResolved()).toList().size();
        this.highSeverityCount = (int) issues.stream().filter(i -> i.getSeverity() == IssueSeverity.HIGH && !i.isResolved()).count();
        this.mediumSeverityCount = (int) issues.stream().filter(i -> i.getSeverity() == IssueSeverity.MEDIUM && !i.isResolved()).count();
        this.lowSeverityCount = (int) issues.stream().filter(i -> i.getSeverity() == IssueSeverity.LOW && !i.isResolved()).count();
        this.infoSeverityCount = (int) issues.stream().filter(i -> i.getSeverity() == IssueSeverity.INFO && !i.isResolved()).count();
        this.resolvedCount = (int) issues.stream().filter(BranchIssue::isResolved).count();
    }

    public int getTotalIssues() { return totalIssues; }
    public int getHighSeverityCount() { return highSeverityCount; }
    public int getMediumSeverityCount() { return mediumSeverityCount; }
    public int getLowSeverityCount() { return lowSeverityCount; }
    public int getInfoSeverityCount() { return infoSeverityCount; }
    public int getResolvedCount() { return resolvedCount; }

    public Long getId() { return id; }

    public Project getProject() { return project; }
    public void setProject(Project project) { this.project = project; }

    public String getBranchName() { return branchName; }
    public void setBranchName(String branchName) { this.branchName = branchName; }

    public String getCommitHash() { return commitHash; }
    public void setCommitHash(String commitHash) { this.commitHash = commitHash; }

    public String getLastSuccessfulCommitHash() { return lastSuccessfulCommitHash; }
    public void setLastSuccessfulCommitHash(String lastSuccessfulCommitHash) { this.lastSuccessfulCommitHash = lastSuccessfulCommitHash; }

    public BranchHealthStatus getHealthStatus() { return healthStatus; }
    public void setHealthStatus(BranchHealthStatus healthStatus) { this.healthStatus = healthStatus; }

    public int getConsecutiveFailures() { return consecutiveFailures; }
    public void setConsecutiveFailures(int consecutiveFailures) { this.consecutiveFailures = consecutiveFailures; }

    public OffsetDateTime getLastHealthCheckAt() { return lastHealthCheckAt; }
    public void setLastHealthCheckAt(OffsetDateTime lastHealthCheckAt) { this.lastHealthCheckAt = lastHealthCheckAt; }

    /**
     * Mark this branch as HEALTHY after a successful analysis.
     * Sets lastSuccessfulCommitHash, resets consecutive failures, and updates health check timestamp.
     */
    public void markHealthy(String commitHash) {
        this.lastSuccessfulCommitHash = commitHash;
        this.healthStatus = BranchHealthStatus.HEALTHY;
        this.consecutiveFailures = 0;
        this.lastHealthCheckAt = OffsetDateTime.now();
    }

    /**
     * Mark this branch as STALE after a failed analysis.
     * Increments consecutive failures counter and updates health check timestamp.
     */
    public void markStale() {
        this.healthStatus = BranchHealthStatus.STALE;
        this.consecutiveFailures++;
        this.lastHealthCheckAt = OffsetDateTime.now();
    }

    public OffsetDateTime getCreatedAt() { return createdAt; }
    public OffsetDateTime getUpdatedAt() { return updatedAt; }

    public List<BranchIssue> getIssues() { return issues; }
    public void setIssues(List<BranchIssue> issues) {
        this.issues = issues;
        updateIssueCounts();
    }
}
