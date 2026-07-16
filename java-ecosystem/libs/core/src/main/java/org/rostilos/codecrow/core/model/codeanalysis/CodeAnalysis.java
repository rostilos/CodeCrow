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
                        name = "uq_code_analysis_execution_id",
                        columnNames = {"execution_id"}
                )
        }
)
public class CodeAnalysis {

    private static final String EXECUTION_ID_PATTERN =
            "[A-Za-z0-9][A-Za-z0-9._:-]{0,159}";
    private static final String MANIFEST_DIGEST_PATTERN = "[0-9a-f]{64}";

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

    @Column(name = "commit_hash", length = 64)
    private String commitHash;

    /**
     * Immutable execution identity for candidate-path analyses. Both fields stay
     * null for explicitly legacy analyses.
     */
    @Column(name = "execution_id", length = 160, updatable = false)
    private String executionId;

    @Column(name = "artifact_manifest_digest", length = 64, updatable = false)
    private String artifactManifestDigest;

    @Column(name = "diff_fingerprint", length = 64)
    private String diffFingerprint;

    @Column(name = "target_branch_name")
    private String branchName;

    @Column(name = "source_branch_name")
    private String sourceBranchName;

    @Column(name = "task_id", length = 128)
    private String taskId;

    @Column(name = "task_summary", length = 512)
    private String taskSummary;

    @Column(name = "comment", columnDefinition = "TEXT")
    private String comment;

    @Column(name = "status", nullable = false, length = 20)
    @Enumerated(EnumType.STRING)
    private AnalysisStatus status = AnalysisStatus.ACCEPTED;

    @Column(name = "analysis_result", length = 20)
    @Enumerated(EnumType.STRING)
    private AnalysisResult analysisResult;

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

    @Column(name = "created_at", nullable = false, updatable = false)
    private OffsetDateTime createdAt = OffsetDateTime.now();

    @Column(name = "updated_at", nullable = false)
    private OffsetDateTime updatedAt = OffsetDateTime.now();

    @OneToMany(mappedBy = "analysis", cascade = CascadeType.ALL, orphanRemoval = true, fetch = FetchType.LAZY)
    private List<CodeAnalysisIssue> issues = new ArrayList<>();

    @Column(name = "pr_version")
    private Integer prVersion;

    /**
     * If this analysis was cloned from a previous analysis (cache hit on fingerprint
     * or commit-hash), stores the source analysis ID for lineage tracking.
     * Null for analyses produced by fresh AI inference.
     */
    @Column(name = "cloned_from_analysis_id")
    private Long clonedFromAnalysisId;

    @PreUpdate
    public void onUpdate() {
        this.updatedAt = OffsetDateTime.now();
    }

    public void updateIssueCounts() {
        this.totalIssues = (int) issues.stream().filter(i -> !i.isResolved()).count();
        this.highSeverityCount = (int) issues.stream().filter(i -> i.getSeverity() == IssueSeverity.HIGH && !i.isResolved()).count();
        this.mediumSeverityCount = (int) issues.stream().filter(i -> i.getSeverity() == IssueSeverity.MEDIUM && !i.isResolved()).count();
        this.lowSeverityCount = (int) issues.stream().filter(i -> i.getSeverity() == IssueSeverity.LOW && !i.isResolved()).count();
        this.infoSeverityCount = (int) issues.stream().filter(i -> i.getSeverity() == IssueSeverity.INFO && !i.isResolved()).count();
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

    public String getExecutionId() { return executionId; }

    public String getArtifactManifestDigest() { return artifactManifestDigest; }

    public boolean hasExecutionIdentity() {
        return executionId != null && artifactManifestDigest != null;
    }

    /**
     * Binds a newly-created candidate analysis to its immutable input manifest.
     * Repeating the same binding is idempotent; replacing or partially supplying
     * an identity is rejected before persistence. The database independently
     * enforces the same write-once invariant.
     */
    public void bindExecutionIdentity(
            String executionId,
            String artifactManifestDigest) {
        if (executionId == null || !executionId.matches(EXECUTION_ID_PATTERN)) {
            throw new IllegalArgumentException("invalid candidate executionId");
        }
        if (artifactManifestDigest == null
                || !artifactManifestDigest.matches(MANIFEST_DIGEST_PATTERN)) {
            throw new IllegalArgumentException(
                    "invalid candidate artifactManifestDigest");
        }
        if (this.executionId != null || this.artifactManifestDigest != null) {
            if (executionId.equals(this.executionId)
                    && artifactManifestDigest.equals(this.artifactManifestDigest)) {
                return;
            }
            throw new IllegalStateException(
                    "candidate execution identity is immutable once bound");
        }
        this.executionId = executionId;
        this.artifactManifestDigest = artifactManifestDigest;
    }

    public String getDiffFingerprint() { return diffFingerprint; }
    public void setDiffFingerprint(String diffFingerprint) { this.diffFingerprint = diffFingerprint; }

    public String getBranchName() { return branchName; }
    public void setBranchName(String branchName) { this.branchName = branchName; }

    public String getSourceBranchName() { return sourceBranchName; }
    public void setSourceBranchName(String sourceBranchName) { this.sourceBranchName = sourceBranchName; }

    public String getTaskId() { return taskId; }
    public void setTaskId(String taskId) { this.taskId = taskId; }

    public String getTaskSummary() { return taskSummary; }
    public void setTaskSummary(String taskSummary) { this.taskSummary = taskSummary; }

    public String getComment() { return comment; }
    public void setComment(String comment) { this.comment = comment; }

    public AnalysisStatus getStatus() { return status; }
    public void setStatus(AnalysisStatus status) { this.status = status; }

    public AnalysisResult getAnalysisResult() { return analysisResult; }
    public void setAnalysisResult(AnalysisResult analysisResult) { this.analysisResult = analysisResult; }

    public int getTotalIssues() { return totalIssues; }
    public int getHighSeverityCount() { return highSeverityCount; }
    public int getMediumSeverityCount() { return mediumSeverityCount; }
    public int getLowSeverityCount() { return lowSeverityCount; }
    public int getInfoSeverityCount() { return infoSeverityCount; }
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

    public Long getClonedFromAnalysisId() {
        return clonedFromAnalysisId;
    }
    public void setClonedFromAnalysisId(Long clonedFromAnalysisId) {
        this.clonedFromAnalysisId = clonedFromAnalysisId;
    }
}
