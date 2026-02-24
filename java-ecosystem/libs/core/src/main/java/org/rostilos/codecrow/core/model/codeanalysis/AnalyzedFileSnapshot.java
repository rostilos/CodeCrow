package org.rostilos.codecrow.core.model.codeanalysis;

import jakarta.persistence.*;

import java.time.OffsetDateTime;

/**
 * A snapshot linking a specific analysis to the file content that was analyzed.
 * One row per (analysis, filePath) pair. Multiple snapshots may reference the
 * same {@link AnalyzedFileContent} row when the file hasn't changed between analyses.
 */
@Entity
@Table(
        name = "analyzed_file_snapshot",
        uniqueConstraints = {
                @UniqueConstraint(
                        name = "uq_analyzed_file_snapshot_analysis_path",
                        columnNames = {"analysis_id", "file_path"}
                )
        }
)
public class AnalyzedFileSnapshot {

    @Id
    @GeneratedValue(strategy = GenerationType.IDENTITY)
    @Column(nullable = false, updatable = false)
    private Long id;

    @ManyToOne(fetch = FetchType.LAZY)
    @JoinColumn(name = "analysis_id", nullable = false)
    private CodeAnalysis analysis;

    /** Repo-relative file path (e.g. "src/main/java/com/example/Foo.java"). */
    @Column(name = "file_path", nullable = false, length = 500)
    private String filePath;

    @ManyToOne(fetch = FetchType.LAZY)
    @JoinColumn(name = "content_id", nullable = false)
    private AnalyzedFileContent fileContent;

    /** Commit hash at the time this snapshot was taken. */
    @Column(name = "commit_hash", length = 40)
    private String commitHash;

    @Column(name = "created_at", nullable = false, updatable = false)
    private OffsetDateTime createdAt = OffsetDateTime.now();

    // ── Getters / Setters ────────────────────────────────────────────────

    public Long getId() { return id; }

    public CodeAnalysis getAnalysis() { return analysis; }
    public void setAnalysis(CodeAnalysis analysis) { this.analysis = analysis; }

    public String getFilePath() { return filePath; }
    public void setFilePath(String filePath) { this.filePath = filePath; }

    public AnalyzedFileContent getFileContent() { return fileContent; }
    public void setFileContent(AnalyzedFileContent fileContent) { this.fileContent = fileContent; }

    public String getCommitHash() { return commitHash; }
    public void setCommitHash(String commitHash) { this.commitHash = commitHash; }

    public OffsetDateTime getCreatedAt() { return createdAt; }
}
