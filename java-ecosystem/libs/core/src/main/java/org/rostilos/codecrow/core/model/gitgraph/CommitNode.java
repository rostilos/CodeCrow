package org.rostilos.codecrow.core.model.gitgraph;

import jakarta.persistence.*;
import org.rostilos.codecrow.core.model.codeanalysis.CodeAnalysis;
import org.rostilos.codecrow.core.model.project.Project;

import java.time.OffsetDateTime;
import java.util.HashSet;
import java.util.Set;

@Entity
@Table(
        name = "git_commit_node",
        uniqueConstraints = {
                @UniqueConstraint(
                        name = "uq_git_commit_node_project_hash",
                        columnNames = {"project_id", "commit_hash"}
                )
        },
        indexes = {
                @Index(name = "idx_git_commit_node_hash", columnList = "commit_hash"),
                @Index(name = "idx_git_commit_node_status", columnList = "analysis_status")
        }
)
public class CommitNode {

    @Id
    @GeneratedValue(strategy = GenerationType.IDENTITY)
    @Column(nullable = false, updatable = false)
    private Long id;

    @ManyToOne(fetch = FetchType.LAZY)
    @JoinColumn(name = "project_id", nullable = false)
    private Project project;

    @Column(name = "commit_hash", nullable = false, length = 40)
    private String commitHash;

    @Column(name = "author_name")
    private String authorName;

    @Column(name = "author_email")
    private String authorEmail;

    @Column(name = "commit_message", columnDefinition = "TEXT")
    private String commitMessage;

    @Column(name = "commit_timestamp")
    private OffsetDateTime commitTimestamp;

    @Column(name = "created_at", nullable = false, updatable = false)
    private OffsetDateTime createdAt = OffsetDateTime.now();

    // ── Analysis tracking ──────────────────────────────────────────────

    @Enumerated(EnumType.STRING)
    @Column(name = "analysis_status", nullable = false, length = 20)
    private CommitAnalysisStatus analysisStatus = CommitAnalysisStatus.NOT_ANALYZED;

    @Column(name = "analyzed_at")
    private OffsetDateTime analyzedAt;

    /**
     * The CodeAnalysis that covered this commit. Null until analysisStatus = ANALYZED.
     * Multiple commits may point to the same analysis (e.g. a delta diff covering 5 commits).
     */
    @ManyToOne(fetch = FetchType.LAZY)
    @JoinColumn(name = "analysis_id")
    private CodeAnalysis analysis;

    // ── DAG edges ──────────────────────────────────────────────────────

    @ManyToMany(fetch = FetchType.LAZY)
    @JoinTable(
            name = "git_commit_edge",
            joinColumns = @JoinColumn(name = "child_commit_id"),
            inverseJoinColumns = @JoinColumn(name = "parent_commit_id")
    )
    private Set<CommitNode> parents = new HashSet<>();

    // ── Getters / Setters ──────────────────────────────────────────────

    public Long getId() {
        return id;
    }

    public void setId(Long id) {
        this.id = id;
    }

    public Project getProject() {
        return project;
    }

    public void setProject(Project project) {
        this.project = project;
    }

    public String getCommitHash() {
        return commitHash;
    }

    public void setCommitHash(String commitHash) {
        this.commitHash = commitHash;
    }

    public String getAuthorName() {
        return authorName;
    }

    public void setAuthorName(String authorName) {
        this.authorName = authorName;
    }

    public String getAuthorEmail() {
        return authorEmail;
    }

    public void setAuthorEmail(String authorEmail) {
        this.authorEmail = authorEmail;
    }

    public String getCommitMessage() {
        return commitMessage;
    }

    public void setCommitMessage(String commitMessage) {
        this.commitMessage = commitMessage;
    }

    public OffsetDateTime getCommitTimestamp() {
        return commitTimestamp;
    }

    public void setCommitTimestamp(OffsetDateTime commitTimestamp) {
        this.commitTimestamp = commitTimestamp;
    }

    public OffsetDateTime getCreatedAt() {
        return createdAt;
    }

    public void setCreatedAt(OffsetDateTime createdAt) {
        this.createdAt = createdAt;
    }

    public CommitAnalysisStatus getAnalysisStatus() {
        return analysisStatus;
    }

    public void setAnalysisStatus(CommitAnalysisStatus analysisStatus) {
        this.analysisStatus = analysisStatus;
    }

    public OffsetDateTime getAnalyzedAt() {
        return analyzedAt;
    }

    public void setAnalyzedAt(OffsetDateTime analyzedAt) {
        this.analyzedAt = analyzedAt;
    }

    public CodeAnalysis getAnalysis() {
        return analysis;
    }

    public void setAnalysis(CodeAnalysis analysis) {
        this.analysis = analysis;
    }

    public Set<CommitNode> getParents() {
        return parents;
    }

    public void setParents(Set<CommitNode> parents) {
        this.parents = parents;
    }

    // ── Convenience ────────────────────────────────────────────────────

    public boolean isAnalyzed() {
        return analysisStatus == CommitAnalysisStatus.ANALYZED;
    }

    /**
     * Mark this commit as covered by a successful analysis.
     */
    public void markAnalyzed(CodeAnalysis coveringAnalysis) {
        this.analysisStatus = CommitAnalysisStatus.ANALYZED;
        this.analyzedAt = OffsetDateTime.now();
        this.analysis = coveringAnalysis;
    }

    /**
     * Mark this commit's analysis as failed (eligible for retry).
     */
    public void markFailed() {
        this.analysisStatus = CommitAnalysisStatus.FAILED;
        this.analyzedAt = null;
        this.analysis = null;
    }
}
