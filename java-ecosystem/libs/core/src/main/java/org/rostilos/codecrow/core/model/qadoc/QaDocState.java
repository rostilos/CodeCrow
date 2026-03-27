package org.rostilos.codecrow.core.model.qadoc;

import jakarta.persistence.*;
import org.rostilos.codecrow.core.model.project.Project;

import java.time.OffsetDateTime;
import java.util.Arrays;
import java.util.Collections;
import java.util.Set;
import java.util.stream.Collectors;

/**
 * Tracks QA auto-documentation generation state per (project, task).
 * <p>
 * Replaces the insecure Jira-comment-marker–based tracking with a
 * server-side record that stores the last analyzed commit hash and the
 * set of documented PR numbers. This enables secure delta-diff computation
 * for same-PR re-runs without trusting externally-editable Jira comments.
 */
@Entity
@Table(
        name = "qa_doc_state",
        uniqueConstraints = {
                @UniqueConstraint(
                        name = "uq_qa_doc_state_project_task",
                        columnNames = {"project_id", "task_id"}
                )
        },
        indexes = {
                @Index(name = "idx_qa_doc_state_project", columnList = "project_id"),
                @Index(name = "idx_qa_doc_state_task", columnList = "project_id, task_id")
        }
)
public class QaDocState {

    @Id
    @GeneratedValue(strategy = GenerationType.IDENTITY)
    @Column(nullable = false, updatable = false)
    private Long id;

    @ManyToOne(fetch = FetchType.LAZY)
    @JoinColumn(name = "project_id", nullable = false)
    private Project project;

    /**
     * The task-management issue key (e.g. PROJ-123). Scoped to a project.
     */
    @Column(name = "task_id", nullable = false, length = 128)
    private String taskId;

    /**
     * The commit hash from the last successful QA doc generation.
     * Used to compute delta diffs for same-PR re-runs.
     */
    @Column(name = "last_commit_hash", length = 40)
    private String lastCommitHash;

    /**
     * FK to the CodeAnalysis that produced the last QA doc.
     * Nullable — set to NULL if the analysis is deleted.
     */
    @Column(name = "last_analysis_id")
    private Long lastAnalysisId;

    /**
     * Comma-separated list of PR numbers already documented for this task.
     * Example: "42,57,63". Empty string means no PRs documented yet.
     */
    @Column(name = "documented_pr_numbers", nullable = false, length = 4096)
    private String documentedPrNumbers = "";

    @Column(name = "last_generated_at")
    private OffsetDateTime lastGeneratedAt;

    @Column(name = "created_at", nullable = false, updatable = false)
    private OffsetDateTime createdAt = OffsetDateTime.now();

    @Column(name = "updated_at", nullable = false)
    private OffsetDateTime updatedAt = OffsetDateTime.now();

    // ── Constructors ───────────────────────────────────────────────────

    public QaDocState() {
    }

    public QaDocState(Project project, String taskId) {
        this.project = project;
        this.taskId = taskId;
        this.createdAt = OffsetDateTime.now();
        this.updatedAt = OffsetDateTime.now();
    }

    // ── PR number helpers ──────────────────────────────────────────────

    /**
     * Returns the documented PR numbers as an unmodifiable set of Longs.
     */
    public Set<Long> getDocumentedPrNumbersSet() {
        if (documentedPrNumbers == null || documentedPrNumbers.isBlank()) {
            return Collections.emptySet();
        }
        return Arrays.stream(documentedPrNumbers.split(","))
                .map(String::trim)
                .filter(s -> !s.isEmpty())
                .map(Long::parseLong)
                .collect(Collectors.toUnmodifiableSet());
    }

    /**
     * Replaces the documented PR numbers from a set of Longs.
     */
    public void setDocumentedPrNumbersFromSet(Set<Long> prNumbers) {
        if (prNumbers == null || prNumbers.isEmpty()) {
            this.documentedPrNumbers = "";
        } else {
            this.documentedPrNumbers = prNumbers.stream()
                    .sorted()
                    .map(String::valueOf)
                    .collect(Collectors.joining(","));
        }
    }

    /**
     * Adds a single PR number to the documented set (idempotent).
     */
    public void addDocumentedPrNumber(Long prNumber) {
        if (prNumber == null) return;
        Set<Long> prs = new java.util.HashSet<>(getDocumentedPrNumbersSet());
        prs.add(prNumber);
        setDocumentedPrNumbersFromSet(prs);
    }

    /**
     * Returns true if the given PR number is already documented.
     */
    public boolean isPrDocumented(Long prNumber) {
        return prNumber != null && getDocumentedPrNumbersSet().contains(prNumber);
    }

    /**
     * Records a successful generation: updates commit hash, analysis id,
     * adds the PR number, and bumps timestamps.
     */
    public void recordGeneration(String commitHash, Long analysisId, Long prNumber) {
        this.lastCommitHash = commitHash;
        this.lastAnalysisId = analysisId;
        this.lastGeneratedAt = OffsetDateTime.now();
        this.updatedAt = OffsetDateTime.now();
        addDocumentedPrNumber(prNumber);
    }

    // ── Getters / Setters ──────────────────────────────────────────────

    public Long getId() { return id; }
    public void setId(Long id) { this.id = id; }

    public Project getProject() { return project; }
    public void setProject(Project project) { this.project = project; }

    public String getTaskId() { return taskId; }
    public void setTaskId(String taskId) { this.taskId = taskId; }

    public String getLastCommitHash() { return lastCommitHash; }
    public void setLastCommitHash(String lastCommitHash) { this.lastCommitHash = lastCommitHash; }

    public Long getLastAnalysisId() { return lastAnalysisId; }
    public void setLastAnalysisId(Long lastAnalysisId) { this.lastAnalysisId = lastAnalysisId; }

    public String getDocumentedPrNumbers() { return documentedPrNumbers; }
    public void setDocumentedPrNumbers(String documentedPrNumbers) { this.documentedPrNumbers = documentedPrNumbers; }

    public OffsetDateTime getLastGeneratedAt() { return lastGeneratedAt; }
    public void setLastGeneratedAt(OffsetDateTime lastGeneratedAt) { this.lastGeneratedAt = lastGeneratedAt; }

    public OffsetDateTime getCreatedAt() { return createdAt; }
    public void setCreatedAt(OffsetDateTime createdAt) { this.createdAt = createdAt; }

    public OffsetDateTime getUpdatedAt() { return updatedAt; }
    public void setUpdatedAt(OffsetDateTime updatedAt) { this.updatedAt = updatedAt; }
}
