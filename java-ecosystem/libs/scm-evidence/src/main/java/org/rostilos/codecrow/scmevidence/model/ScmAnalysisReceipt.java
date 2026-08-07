package org.rostilos.codecrow.scmevidence.model;

import jakarta.persistence.*;

import java.time.OffsetDateTime;

@Entity
@Table(name = "scm_analysis_receipt", uniqueConstraints = @UniqueConstraint(
        name = "uq_scm_analysis_receipt_context",
        columnNames = {"project_id", "commit_evidence_id", "context_key"}))
public class ScmAnalysisReceipt {
    @Id @GeneratedValue(strategy = GenerationType.IDENTITY)
    private Long id;
    @Column(name = "project_id", nullable = false)
    private Long projectId;
    @ManyToOne(fetch = FetchType.LAZY, optional = false)
    @JoinColumn(name = "commit_evidence_id", nullable = false)
    private ScmCommitEvidence commitEvidence;
    @Column(name = "source_branch", length = 256)
    private String sourceBranch;
    @Column(name = "target_branch", nullable = false, length = 256)
    private String targetBranch;
    @Column(name = "target_base_revision", length = 64)
    private String targetBaseRevision;
    @Column(name = "analysis_id")
    private Long analysisId;
    @Column(name = "analysis_type", nullable = false, length = 40)
    private String analysisType;
    @Column(name = "context_key", nullable = false, length = 64)
    private String contextKey;
    @Column(name = "analyzed_at", nullable = false)
    private OffsetDateTime analyzedAt = OffsetDateTime.now();

    public ScmAnalysisReceipt() {}

    public ScmAnalysisReceipt(Long projectId, ScmCommitEvidence commitEvidence,
            String sourceBranch, String targetBranch, String targetBaseRevision,
            Long analysisId, String analysisType, String contextKey) {
        this.projectId = projectId;
        this.commitEvidence = commitEvidence;
        this.sourceBranch = sourceBranch;
        this.targetBranch = targetBranch;
        this.targetBaseRevision = targetBaseRevision;
        this.analysisId = analysisId;
        this.analysisType = analysisType;
        this.contextKey = contextKey;
    }

    public ScmCommitEvidence getCommitEvidence() { return commitEvidence; }
    public String getSourceBranch() { return sourceBranch; }
    public String getTargetBranch() { return targetBranch; }
    public String getTargetBaseRevision() { return targetBaseRevision; }
    public Long getAnalysisId() { return analysisId; }
    public String getAnalysisType() { return analysisType; }
}
