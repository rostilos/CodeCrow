package org.rostilos.codecrow.commitgraph.model;

import jakarta.persistence.*;
import org.rostilos.codecrow.core.model.codeanalysis.AnalysisType;
import org.rostilos.codecrow.core.model.project.Project;

import java.time.OffsetDateTime;

/**
 * Lightweight record tracking which commits have been analyzed.
 * <p>
 * Replaces the old {@code CommitNode} entity and its DAG edge table.
 * Instead of reconstructing a full git graph in the database, we simply
 * record "commit X was analyzed at time T by analysis Y".
 * <p>
 * The analysis flow uses this table via set subtraction:
 * {@code commits_in_push - already_analyzed = commits_needing_analysis}
 */
@Entity
@Table(
        name = "analyzed_commit",
        uniqueConstraints = {
                @UniqueConstraint(
                        name = "uq_analyzed_commit_project_hash",
                        columnNames = {"project_id", "commit_hash"}
                )
        },
        indexes = {
                @Index(name = "idx_analyzed_commit_project", columnList = "project_id"),
                @Index(name = "idx_analyzed_commit_hash", columnList = "commit_hash")
        }
)
public class AnalyzedCommit {

    @Id
    @GeneratedValue(strategy = GenerationType.IDENTITY)
    @Column(nullable = false, updatable = false)
    private Long id;

    @ManyToOne(fetch = FetchType.LAZY)
    @JoinColumn(name = "project_id", nullable = false)
    private Project project;

    @Column(name = "commit_hash", nullable = false, length = 40)
    private String commitHash;

    @Column(name = "analyzed_at", nullable = false)
    private OffsetDateTime analyzedAt = OffsetDateTime.now();

    /**
     * Optional link to the CodeAnalysis that covered this commit.
     * Null for branch analysis (which doesn't produce a CodeAnalysis record).
     */
    @Column(name = "analysis_id")
    private Long analysisId;

    /**
     * The type of analysis that covered this commit.
     */
    @Enumerated(EnumType.STRING)
    @Column(name = "analysis_type", length = 30)
    private AnalysisType analysisType;

    // ── Constructors ───────────────────────────────────────────────────

    public AnalyzedCommit() {
    }

    public AnalyzedCommit(Project project, String commitHash, AnalysisType analysisType) {
        this.project = project;
        this.commitHash = commitHash;
        this.analysisType = analysisType;
        this.analyzedAt = OffsetDateTime.now();
    }

    public AnalyzedCommit(Project project, String commitHash, Long analysisId, AnalysisType analysisType) {
        this(project, commitHash, analysisType);
        this.analysisId = analysisId;
    }

    // ── Getters / Setters ──────────────────────────────────────────────

    public Long getId() { return id; }
    public void setId(Long id) { this.id = id; }

    public Project getProject() { return project; }
    public void setProject(Project project) { this.project = project; }

    public String getCommitHash() { return commitHash; }
    public void setCommitHash(String commitHash) { this.commitHash = commitHash; }

    public OffsetDateTime getAnalyzedAt() { return analyzedAt; }
    public void setAnalyzedAt(OffsetDateTime analyzedAt) { this.analyzedAt = analyzedAt; }

    public Long getAnalysisId() { return analysisId; }
    public void setAnalysisId(Long analysisId) { this.analysisId = analysisId; }

    public AnalysisType getAnalysisType() { return analysisType; }
    public void setAnalysisType(AnalysisType analysisType) { this.analysisType = analysisType; }
}
