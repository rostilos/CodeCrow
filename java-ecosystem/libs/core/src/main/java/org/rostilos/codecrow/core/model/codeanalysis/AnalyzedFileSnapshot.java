package org.rostilos.codecrow.core.model.codeanalysis;

import jakarta.persistence.*;
import org.rostilos.codecrow.core.model.branch.Branch;
import org.rostilos.codecrow.core.model.pullrequest.PullRequest;

import java.time.OffsetDateTime;

/**
 * A snapshot linking a file to its content at a given commit.
 * <p>
 * For <b>PR analyses</b> the snapshot is keyed on {@code (pull_request_id, file_path)}
 * so that files accumulate across analysis iterations — the 2nd run adds/updates
 * only the changed files while all previously-seen files remain visible.
 * <p>
 * For <b>branch analyses</b> the snapshot is keyed on {@code (branch_id, file_path)}
 * using the first-class Branch FK. This replaces the legacy approach that used
 * {@code (analysis_id, file_path)} and DISTINCT ON queries across all analyses.
 * The branch-level upsert model means each branch has exactly one snapshot per file,
 * always pointing to the latest content.
 * <p>
 * Multiple snapshots may reference the same {@link AnalyzedFileContent} row when
 * the file hasn't changed between analyses (content-addressed dedup by SHA-256).
 */
@Entity
@Table(name = "analyzed_file_snapshot")
public class AnalyzedFileSnapshot {

    @Id
    @GeneratedValue(strategy = GenerationType.IDENTITY)
    @Column(nullable = false, updatable = false)
    private Long id;

    /** Nullable — set for branch analyses (legacy), may be null for PR-level snapshots. */
    @ManyToOne(fetch = FetchType.LAZY)
    @JoinColumn(name = "analysis_id")
    private CodeAnalysis analysis;

    /** Nullable — set for PR analyses. Files accumulate per PR across iterations. */
    @ManyToOne(fetch = FetchType.LAZY)
    @JoinColumn(name = "pull_request_id")
    private PullRequest pullRequest;

    /**
     * Nullable — set for branch-level snapshots. Provides a direct FK to the Branch
     * entity, replacing the legacy approach of joining through code_analysis.
     * Keyed on {@code (branch_id, file_path)} for upsert semantics.
     */
    @ManyToOne(fetch = FetchType.LAZY)
    @JoinColumn(name = "branch_id")
    private Branch branch;

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

    public PullRequest getPullRequest() { return pullRequest; }
    public void setPullRequest(PullRequest pullRequest) { this.pullRequest = pullRequest; }

    public Branch getBranch() { return branch; }
    public void setBranch(Branch branch) { this.branch = branch; }

    public String getFilePath() { return filePath; }
    public void setFilePath(String filePath) { this.filePath = filePath; }

    public AnalyzedFileContent getFileContent() { return fileContent; }
    public void setFileContent(AnalyzedFileContent fileContent) { this.fileContent = fileContent; }

    public String getCommitHash() { return commitHash; }
    public void setCommitHash(String commitHash) { this.commitHash = commitHash; }

    public OffsetDateTime getCreatedAt() { return createdAt; }
}
