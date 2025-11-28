package org.rostilos.codecrow.core.model.codeanalysis;

import jakarta.persistence.*;
import org.rostilos.codecrow.core.model.project.Project;

import java.time.OffsetDateTime;
import java.util.List;
import java.util.ArrayList;

@Entity
@Table(
        name = "code_analysis",
        uniqueConstraints = {
                @UniqueConstraint(
                        name = "uq_code_analysis_project_commit",
                        columnNames = {"project_id", "commit_hash", "pr_number"}
                )
        }
)
public class CodeAnalysis {

    @Id
    @GeneratedValue(strategy = GenerationType.IDENTITY)
    @Column(nullable = false, updatable = false)
    private Long id;

    @ManyToOne(fetch = FetchType.LAZY)
    @JoinColumn(name = "project_id", nullable = false)
    private Project project;

    @Column(name = "analysis_type", nullable = false, length = 50)
    @Enumerated(EnumType.STRING)
    private AnalysisType analysisType;

    @Column(name = "pr_number")
    private Long prNumber;

    @Column(name = "commit_hash", length = 40)
    private String commitHash;

    @Column(name = "target_branch_name")
    private String branchName;

    @Column(name = "source_branch_name")
    private String sourceBranchName;

    @Column(name = "comment", columnDefinition = "TEXT")
    private String comment;

    @Column(name = "status", nullable = false, length = 20)
    @Enumerated(EnumType.STRING)
    private AnalysisStatus status = AnalysisStatus.ACCEPTED;

    @Column(name = "total_issues", nullable = false)
    private int totalIssues = 0;

    @Column(name = "high_severity_count", nullable = false)
    private int highSeverityCount = 0;

    @Column(name = "medium_severity_count", nullable = false)
    private int mediumSeverityCount = 0;

    @Column(name = "low_severity_count", nullable = false)
    private int lowSeverityCount = 0;

    @Column(name = "resolved_count", nullable = false)
    private int resolvedCount = 0;

    @Column(name = "created_at", nullable = false, updatable = false)
    private OffsetDateTime createdAt = OffsetDateTime.now();

    @Column(name = "updated_at", nullable = false)
    private OffsetDateTime updatedAt = OffsetDateTime.now();

    @OneToMany(mappedBy = "analysis", cascade = CascadeType.ALL, orphanRemoval = true, fetch = FetchType.LAZY)
    private List<CodeAnalysisIssue> issues = new ArrayList<>();

    @Column(name = "pr_version")
    private Integer prVersion;

    @PreUpdate
    public void onUpdate() {
        this.updatedAt = OffsetDateTime.now();
    }

    public void updateIssueCounts() {
        this.totalIssues = issues.size();
        this.highSeverityCount = (int) issues.stream().filter(i -> i.getSeverity() == IssueSeverity.HIGH).count();
        this.mediumSeverityCount = (int) issues.stream().filter(i -> i.getSeverity() == IssueSeverity.MEDIUM).count();
        this.lowSeverityCount = (int) issues.stream().filter(i -> i.getSeverity() == IssueSeverity.LOW).count();
        this.resolvedCount = (int) issues.stream().filter(CodeAnalysisIssue::isResolved).count();
    }

    public Long getId() { return id; }

    public Project getProject() { return project; }
    public void setProject(Project project) { this.project = project; }

    public AnalysisType getAnalysisType() { return analysisType; }
    public void setAnalysisType(AnalysisType analysisType) { this.analysisType = analysisType; }

    public Long getPrNumber() { return prNumber; }
    public void setPrNumber(Long prNumber) { this.prNumber = prNumber; }

    public String getCommitHash() { return commitHash; }
    public void setCommitHash(String commitHash) { this.commitHash = commitHash; }

    public String getBranchName() { return branchName; }
    public void setBranchName(String branchName) { this.branchName = branchName; }

    public String getSourceBranchName() { return sourceBranchName; }
    public void setSourceBranchName(String sourceBranchName) { this.sourceBranchName = sourceBranchName; }

    public String getComment() { return comment; }
    public void setComment(String comment) { this.comment = comment; }

    public AnalysisStatus getStatus() { return status; }
    public void setStatus(AnalysisStatus status) { this.status = status; }

    public int getTotalIssues() { return totalIssues; }
    public int getHighSeverityCount() { return highSeverityCount; }
    public int getMediumSeverityCount() { return mediumSeverityCount; }
    public int getLowSeverityCount() { return lowSeverityCount; }
    public int getResolvedCount() { return resolvedCount; }

    public OffsetDateTime getCreatedAt() { return createdAt; }
    public OffsetDateTime getUpdatedAt() { return updatedAt; }

    public List<CodeAnalysisIssue> getIssues() { return issues; }
    public void setIssues(List<CodeAnalysisIssue> issues) {
        this.issues = issues;
        updateIssueCounts();
    }

    public void addIssue(CodeAnalysisIssue issue) {
        issues.add(issue);
        issue.setAnalysis(this);
        updateIssueCounts();
    }

    public Integer getPrVersion() {
        return prVersion;
    }
    public void setPrVersion(Integer prVersion) {
        this.prVersion = prVersion;
    }
}
