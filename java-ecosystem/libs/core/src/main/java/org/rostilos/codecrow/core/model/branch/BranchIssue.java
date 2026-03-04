package org.rostilos.codecrow.core.model.branch;

import jakarta.persistence.*;
import org.rostilos.codecrow.core.model.codeanalysis.CodeAnalysisIssue;
import org.rostilos.codecrow.core.model.codeanalysis.DetectionSource;
import org.rostilos.codecrow.core.model.codeanalysis.IssueCategory;
import org.rostilos.codecrow.core.model.codeanalysis.IssueScope;
import org.rostilos.codecrow.core.model.codeanalysis.IssueSeverity;
import org.rostilos.codecrow.core.util.tracking.ReconcilableIssue;
import org.rostilos.codecrow.core.util.tracking.TrackingConfidence;

import java.time.OffsetDateTime;

/**
 * Independent branch-level issue entity.
 * <p>
 * Each {@code BranchIssue} is a <b>full copy</b> of the issue data as it exists on
 * the branch.  The original {@link CodeAnalysisIssue} is kept as an optional
 * provenance reference ({@code origin_issue_id}) but is <b>never mutated</b> by
 * branch reconciliation.  All line-number shifts, resolution tracking, and
 * content updates happen exclusively on this entity.
 * <p>
 * Deduplication key: {@code (branch_id, content_fingerprint)}.
 */
@Entity
@Table(name = "branch_issue")
// NOTE: The unique constraint on (branch_id, content_fingerprint) is a PARTIAL index
// (WHERE content_fingerprint IS NOT NULL) and is managed via Flyway / manual DDL,
// not via @UniqueConstraint which cannot express partial indexes.
public class BranchIssue implements ReconcilableIssue {

    @Id
    @GeneratedValue(strategy = GenerationType.IDENTITY)
    @Column(nullable = false, updatable = false)
    private Long id;

    @ManyToOne(fetch = FetchType.LAZY)
    @JoinColumn(name = "branch_id", nullable = false)
    private Branch branch;

    // ── Provenance: optional back-reference to the original PR issue ─────
    // Nullable — kept for traceability only.  Never mutated via this FK.
    @ManyToOne(fetch = FetchType.LAZY)
    @JoinColumn(name = "origin_issue_id")
    private CodeAnalysisIssue originIssue;

    // ── Full issue data (independent copy) ──────────────────────────────

    @Column(name = "severity", nullable = false, length = 10)
    @Enumerated(EnumType.STRING)
    private IssueSeverity severity;

    @Column(name = "file_path", nullable = false, length = 500)
    private String filePath;

    @Column(name = "line_number")
    private Integer lineNumber;

    @Column(name = "end_line_number")
    private Integer endLineNumber;

    /** Start line of the enclosing AST scope (function, class, block). */
    @Column(name = "scope_start_line")
    private Integer scopeStartLine;

    @Column(name = "issue_scope", length = 20)
    @Enumerated(EnumType.STRING)
    private IssueScope issueScope;

    @Column(name = "reason", columnDefinition = "TEXT")
    private String reason;

    @Column(name = "title", length = 255)
    private String title;

    @Column(name = "suggested_fix_description", columnDefinition = "TEXT")
    private String suggestedFixDescription;

    @Column(name = "suggested_fix_diff", columnDefinition = "TEXT")
    private String suggestedFixDiff;

    @Column(name = "issue_category", length = 50)
    @Enumerated(EnumType.STRING)
    private IssueCategory issueCategory;

    @Column(name = "is_resolved", nullable = false)
    private boolean resolved = false;

    // ── Detection provenance ────────────────────────────────────────────

    /** PR number where this issue was first detected. */
    @Column(name = "first_detected_pr_number")
    private Long firstDetectedPrNumber;

    /** Analysis ID from the original detection (immutable). */
    @Column(name = "origin_analysis_id")
    private Long originAnalysisId;

    /** PR number of the original detection analysis. */
    @Column(name = "origin_pr_number")
    private Long originPrNumber;

    /** Commit hash of the original detection analysis. */
    @Column(name = "origin_commit_hash", length = 40)
    private String originCommitHash;

    /** Branch name from the original detection analysis. */
    @Column(name = "origin_branch_name", length = 200)
    private String originBranchName;

    // ── Resolution tracking ─────────────────────────────────────────────

    @Column(name = "resolved_in_pr_number")
    private Long resolvedInPrNumber;

    @Column(name = "resolved_in_commit_hash", length = 40)
    private String resolvedInCommitHash;

    @Column(name = "resolved_description", columnDefinition = "TEXT")
    private String resolvedDescription;

    @Column(name = "resolved_at")
    private OffsetDateTime resolvedAt;

    @Column(name = "resolved_by", length = 100)
    private String resolvedBy;

    // ── VCS author info ─────────────────────────────────────────────────

    @Column(name = "vcs_author_id", length = 100)
    private String vcsAuthorId;

    @Column(name = "vcs_author_username", length = 100)
    private String vcsAuthorUsername;

    // ── Content-based tracking fields ───────────────────────────────────

    /** MD5 hex of the whitespace-normalized source line at detection time. */
    @Column(name = "line_hash", length = 32)
    private String lineHash;

    /** MD5 hex of the ±2 context window around the source line. */
    @Column(name = "line_hash_context", length = 32)
    private String lineHashContext;

    /** SHA-256 hex of (category + lineHash + normalizedTitle) — stable across line shifts. */
    @Column(name = "issue_fingerprint", length = 64)
    private String issueFingerprint;

    /**
     * Category-agnostic fingerprint: SHA-256 of (lineHash + normalizedTitle).
     * Primary dedup key for the (branch_id, content_fingerprint) unique constraint.
     */
    @Column(name = "content_fingerprint", length = 64)
    private String contentFingerprint;

    /**
     * Verbatim source line the LLM referenced when reporting this issue.
     * Used for content-based line anchoring at serve-time and during reconciliation.
     */
    @Column(name = "code_snippet", columnDefinition = "TEXT")
    private String codeSnippet;

    /** Current line number on the branch (may drift from original detection line). */
    @Column(name = "current_line_number")
    private Integer currentLineNumber;

    /** Current end line number on the branch (may drift from original). */
    @Column(name = "current_end_line_number")
    private Integer currentEndLineNumber;

    /** Current start line of the enclosing AST scope on the branch (may drift from original). */
    @Column(name = "current_scope_start_line")
    private Integer currentScopeStartLine;

    /** MD5 hex of the current source line content on the branch. */
    @Column(name = "current_line_hash", length = 32)
    private String currentLineHash;

    /** Commit SHA at which this issue's position was last verified. */
    @Column(name = "last_verified_commit", length = 40)
    private String lastVerifiedCommit;

    /** Confidence level of the last tracking match. */
    @Column(name = "tracking_confidence", length = 30)
    @Enumerated(EnumType.STRING)
    private TrackingConfidence trackingConfidence;

    /**
     * How this issue was originally detected: via PR analysis or via direct push
     * (hybrid branch analysis). Copied from the originating {@link CodeAnalysisIssue}.
     */
    @Column(name = "detection_source", length = 30)
    @Enumerated(EnumType.STRING)
    private DetectionSource detectionSource;

    // ── Timestamps ──────────────────────────────────────────────────────

    @Column(name = "created_at", nullable = false, updatable = false)
    private OffsetDateTime createdAt = OffsetDateTime.now();

    @Column(name = "updated_at", nullable = false)
    private OffsetDateTime updatedAt = OffsetDateTime.now();

    @PreUpdate
    public void onUpdate() {
        this.updatedAt = OffsetDateTime.now();
    }

    // ── Deep-copy factory ───────────────────────────────────────────────

    /**
     * Create a new {@code BranchIssue} as a full deep-copy of a {@link CodeAnalysisIssue}.
     * The original issue is stored as {@code originIssue} for provenance only.
     */
    public static BranchIssue fromCodeAnalysisIssue(CodeAnalysisIssue cai, Branch branch) {
        BranchIssue bi = new BranchIssue();
        bi.setBranch(branch);
        bi.setOriginIssue(cai);

        // Full data copy
        bi.setSeverity(cai.getSeverity());
        bi.setFilePath(cai.getFilePath());
        bi.setLineNumber(cai.getLineNumber());
        bi.setReason(cai.getReason());
        bi.setTitle(cai.getTitle());
        bi.setSuggestedFixDescription(cai.getSuggestedFixDescription());
        bi.setSuggestedFixDiff(cai.getSuggestedFixDiff());
        bi.setIssueCategory(cai.getIssueCategory());
        bi.setResolved(cai.isResolved());
        bi.setIssueScope(cai.getIssueScope());
        bi.setEndLineNumber(cai.getEndLineNumber());
        bi.setScopeStartLine(cai.getScopeStartLine());

        // VCS author
        bi.setVcsAuthorId(cai.getVcsAuthorId());
        bi.setVcsAuthorUsername(cai.getVcsAuthorUsername());

        // Tracking hashes
        bi.setLineHash(cai.getLineHash());
        bi.setLineHashContext(cai.getLineHashContext());
        bi.setIssueFingerprint(cai.getIssueFingerprint());
        bi.setContentFingerprint(cai.getContentFingerprint());
        bi.setCodeSnippet(cai.getCodeSnippet());

        // Initialize current position from detection position
        bi.setCurrentLineNumber(cai.getLineNumber());
        bi.setCurrentLineHash(cai.getLineHash());
        bi.setCurrentEndLineNumber(cai.getEndLineNumber());
        bi.setCurrentScopeStartLine(cai.getScopeStartLine());

        // Detection provenance
        bi.setDetectionSource(cai.getDetectionSource());
        if (cai.getAnalysis() != null) {
            bi.setOriginAnalysisId(cai.getAnalysis().getId());
            bi.setOriginPrNumber(cai.getAnalysis().getPrNumber());
            bi.setOriginCommitHash(cai.getAnalysis().getCommitHash());
            bi.setOriginBranchName(cai.getAnalysis().getBranchName());
            bi.setFirstDetectedPrNumber(cai.getAnalysis().getPrNumber());
        }

        return bi;
    }

    // ── Getters / Setters ───────────────────────────────────────────────

    public Long getId() { return id; }

    public Branch getBranch() { return branch; }
    public void setBranch(Branch branch) { this.branch = branch; }

    public CodeAnalysisIssue getOriginIssue() { return originIssue; }
    public void setOriginIssue(CodeAnalysisIssue originIssue) { this.originIssue = originIssue; }

    /**
     * @deprecated Use {@link #getOriginIssue()} for provenance access.
     *             Most code should read data directly from BranchIssue fields.
     */
    @Deprecated
    public CodeAnalysisIssue getCodeAnalysisIssue() { return originIssue; }
    /**
     * @deprecated Use {@link #setOriginIssue(CodeAnalysisIssue)} instead.
     */
    @Deprecated
    public void setCodeAnalysisIssue(CodeAnalysisIssue cai) { this.originIssue = cai; }

    public IssueSeverity getSeverity() { return severity; }
    public void setSeverity(IssueSeverity severity) { this.severity = severity; }

    public String getFilePath() { return filePath; }
    public void setFilePath(String filePath) { this.filePath = filePath; }

    public Integer getLineNumber() { return lineNumber; }
    public void setLineNumber(Integer lineNumber) { this.lineNumber = lineNumber; }

    /**
     * Returns the current best-known line position.
     * For BranchIssue, this is {@code currentLineNumber} (drifted) if available,
     * otherwise falls back to the original {@code lineNumber}.
     */
    @Override
    public Integer getLine() {
        return currentLineNumber != null ? currentLineNumber : lineNumber;
    }

    public Integer getEndLineNumber() { return endLineNumber; }
    public void setEndLineNumber(Integer endLineNumber) { this.endLineNumber = endLineNumber; }

    public Integer getScopeStartLine() { return scopeStartLine; }
    public void setScopeStartLine(Integer scopeStartLine) { this.scopeStartLine = scopeStartLine; }

    public IssueScope getIssueScope() { return issueScope; }
    public void setIssueScope(IssueScope issueScope) { this.issueScope = issueScope; }

    public String getReason() { return reason; }
    public void setReason(String reason) { this.reason = reason; }

    public String getTitle() { return title; }
    public void setTitle(String title) { this.title = title; }

    public String getSuggestedFixDescription() { return suggestedFixDescription; }
    public void setSuggestedFixDescription(String suggestedFixDescription) { this.suggestedFixDescription = suggestedFixDescription; }

    public String getSuggestedFixDiff() { return suggestedFixDiff; }
    public void setSuggestedFixDiff(String suggestedFixDiff) { this.suggestedFixDiff = suggestedFixDiff; }

    public IssueCategory getIssueCategory() { return issueCategory; }
    public void setIssueCategory(IssueCategory issueCategory) { this.issueCategory = issueCategory; }

    public boolean isResolved() { return resolved; }
    public void setResolved(boolean resolved) { this.resolved = resolved; }

    public Long getFirstDetectedPrNumber() { return firstDetectedPrNumber; }
    public void setFirstDetectedPrNumber(Long firstDetectedPrNumber) { this.firstDetectedPrNumber = firstDetectedPrNumber; }

    public Long getOriginAnalysisId() { return originAnalysisId; }
    public void setOriginAnalysisId(Long originAnalysisId) { this.originAnalysisId = originAnalysisId; }

    public Long getOriginPrNumber() { return originPrNumber; }
    public void setOriginPrNumber(Long originPrNumber) { this.originPrNumber = originPrNumber; }

    public String getOriginCommitHash() { return originCommitHash; }
    public void setOriginCommitHash(String originCommitHash) { this.originCommitHash = originCommitHash; }

    public String getOriginBranchName() { return originBranchName; }
    public void setOriginBranchName(String originBranchName) { this.originBranchName = originBranchName; }

    public Long getResolvedInPrNumber() { return resolvedInPrNumber; }
    public void setResolvedInPrNumber(Long resolvedInPrNumber) { this.resolvedInPrNumber = resolvedInPrNumber; }

    public String getResolvedInCommitHash() { return resolvedInCommitHash; }
    public void setResolvedInCommitHash(String resolvedInCommitHash) { this.resolvedInCommitHash = resolvedInCommitHash; }

    public String getResolvedDescription() { return resolvedDescription; }
    public void setResolvedDescription(String resolvedDescription) { this.resolvedDescription = resolvedDescription; }

    public OffsetDateTime getResolvedAt() { return resolvedAt; }
    public void setResolvedAt(OffsetDateTime resolvedAt) { this.resolvedAt = resolvedAt; }

    public String getResolvedBy() { return resolvedBy; }
    public void setResolvedBy(String resolvedBy) { this.resolvedBy = resolvedBy; }

    public String getVcsAuthorId() { return vcsAuthorId; }
    public void setVcsAuthorId(String vcsAuthorId) { this.vcsAuthorId = vcsAuthorId; }

    public String getVcsAuthorUsername() { return vcsAuthorUsername; }
    public void setVcsAuthorUsername(String vcsAuthorUsername) { this.vcsAuthorUsername = vcsAuthorUsername; }

    public String getLineHash() { return lineHash; }
    public void setLineHash(String lineHash) { this.lineHash = lineHash; }

    public String getLineHashContext() { return lineHashContext; }
    public void setLineHashContext(String lineHashContext) { this.lineHashContext = lineHashContext; }

    public String getIssueFingerprint() { return issueFingerprint; }
    public void setIssueFingerprint(String issueFingerprint) { this.issueFingerprint = issueFingerprint; }

    public String getContentFingerprint() { return contentFingerprint; }
    public void setContentFingerprint(String contentFingerprint) { this.contentFingerprint = contentFingerprint; }

    public String getCodeSnippet() { return codeSnippet; }
    public void setCodeSnippet(String codeSnippet) { this.codeSnippet = codeSnippet; }

    public Integer getCurrentLineNumber() { return currentLineNumber; }
    public void setCurrentLineNumber(Integer currentLineNumber) { this.currentLineNumber = currentLineNumber; }

    public Integer getCurrentEndLineNumber() { return currentEndLineNumber; }
    public void setCurrentEndLineNumber(Integer currentEndLineNumber) { this.currentEndLineNumber = currentEndLineNumber; }

    public Integer getCurrentScopeStartLine() { return currentScopeStartLine; }
    public void setCurrentScopeStartLine(Integer currentScopeStartLine) { this.currentScopeStartLine = currentScopeStartLine; }

    public String getCurrentLineHash() { return currentLineHash; }
    public void setCurrentLineHash(String currentLineHash) { this.currentLineHash = currentLineHash; }

    public String getLastVerifiedCommit() { return lastVerifiedCommit; }
    public void setLastVerifiedCommit(String lastVerifiedCommit) { this.lastVerifiedCommit = lastVerifiedCommit; }

    public TrackingConfidence getTrackingConfidence() { return trackingConfidence; }
    public void setTrackingConfidence(TrackingConfidence trackingConfidence) { this.trackingConfidence = trackingConfidence; }

    public DetectionSource getDetectionSource() { return detectionSource; }
    public void setDetectionSource(DetectionSource detectionSource) { this.detectionSource = detectionSource; }

    public OffsetDateTime getCreatedAt() { return createdAt; }
    public OffsetDateTime getUpdatedAt() { return updatedAt; }
}
