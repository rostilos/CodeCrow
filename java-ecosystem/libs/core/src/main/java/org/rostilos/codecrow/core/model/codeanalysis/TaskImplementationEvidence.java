package org.rostilos.codecrow.core.model.codeanalysis;

import jakarta.persistence.Column;
import jakarta.persistence.Entity;
import jakarta.persistence.FetchType;
import jakarta.persistence.GeneratedValue;
import jakarta.persistence.GenerationType;
import jakarta.persistence.Id;
import jakarta.persistence.JoinColumn;
import jakarta.persistence.ManyToOne;
import jakarta.persistence.Table;
import jakarta.persistence.UniqueConstraint;
import org.rostilos.codecrow.core.model.project.Project;

import java.time.OffsetDateTime;

/**
 * Positive, deterministic implementation evidence associated with one PR
 * analysis and task. This is internal analysis state and is never rendered in
 * a VCS or task-management comment.
 */
@Entity
@Table(
        name = "task_implementation_evidence",
        uniqueConstraints = @UniqueConstraint(
                name = "uq_task_implementation_evidence_analysis_fingerprint",
                columnNames = {"analysis_id", "content_fingerprint"}
        )
)
public class TaskImplementationEvidence {

    @Id
    @GeneratedValue(strategy = GenerationType.IDENTITY)
    @Column(nullable = false, updatable = false)
    private Long id;

    @ManyToOne(fetch = FetchType.LAZY, optional = false)
    @JoinColumn(name = "analysis_id", nullable = false)
    private CodeAnalysis analysis;

    @ManyToOne(fetch = FetchType.LAZY, optional = false)
    @JoinColumn(name = "project_id", nullable = false)
    private Project project;

    @Column(name = "task_id", nullable = false, length = 128)
    private String taskId;

    @Column(name = "pr_number", nullable = false)
    private Long prNumber;

    @Column(name = "commit_hash", nullable = false, length = 64)
    private String commitHash;

    @Column(name = "source", nullable = false, length = 40)
    private String source;

    @Column(name = "evidence_ref", nullable = false, length = 32)
    private String evidenceRef;

    @Column(name = "file_path", nullable = false, length = 2048)
    private String filePath;

    @Column(name = "hunk_id", nullable = false, length = 160)
    private String hunkId;

    @Column(name = "line_start", nullable = false)
    private Integer lineStart;

    @Column(name = "line_end", nullable = false)
    private Integer lineEnd;

    @Column(name = "excerpt", nullable = false, columnDefinition = "TEXT")
    private String excerpt;

    @Column(name = "full_evidence_complete", nullable = false)
    private boolean fullEvidenceComplete;

    @Column(name = "content_fingerprint", nullable = false, length = 64)
    private String contentFingerprint;

    @Column(name = "created_at", nullable = false, updatable = false)
    private OffsetDateTime createdAt = OffsetDateTime.now();

    public Long getId() {
        return id;
    }

    public CodeAnalysis getAnalysis() {
        return analysis;
    }

    public void setAnalysis(CodeAnalysis analysis) {
        this.analysis = analysis;
    }

    public Project getProject() {
        return project;
    }

    public void setProject(Project project) {
        this.project = project;
    }

    public String getTaskId() {
        return taskId;
    }

    public void setTaskId(String taskId) {
        this.taskId = taskId;
    }

    public Long getPrNumber() {
        return prNumber;
    }

    public void setPrNumber(Long prNumber) {
        this.prNumber = prNumber;
    }

    public String getCommitHash() {
        return commitHash;
    }

    public void setCommitHash(String commitHash) {
        this.commitHash = commitHash;
    }

    public String getSource() {
        return source;
    }

    public void setSource(String source) {
        this.source = source;
    }

    public String getEvidenceRef() {
        return evidenceRef;
    }

    public void setEvidenceRef(String evidenceRef) {
        this.evidenceRef = evidenceRef;
    }

    public String getFilePath() {
        return filePath;
    }

    public void setFilePath(String filePath) {
        this.filePath = filePath;
    }

    public String getHunkId() {
        return hunkId;
    }

    public void setHunkId(String hunkId) {
        this.hunkId = hunkId;
    }

    public Integer getLineStart() {
        return lineStart;
    }

    public void setLineStart(Integer lineStart) {
        this.lineStart = lineStart;
    }

    public Integer getLineEnd() {
        return lineEnd;
    }

    public void setLineEnd(Integer lineEnd) {
        this.lineEnd = lineEnd;
    }

    public String getExcerpt() {
        return excerpt;
    }

    public void setExcerpt(String excerpt) {
        this.excerpt = excerpt;
    }

    public boolean isFullEvidenceComplete() {
        return fullEvidenceComplete;
    }

    public void setFullEvidenceComplete(boolean fullEvidenceComplete) {
        this.fullEvidenceComplete = fullEvidenceComplete;
    }

    public String getContentFingerprint() {
        return contentFingerprint;
    }

    public void setContentFingerprint(String contentFingerprint) {
        this.contentFingerprint = contentFingerprint;
    }

    public OffsetDateTime getCreatedAt() {
        return createdAt;
    }
}
