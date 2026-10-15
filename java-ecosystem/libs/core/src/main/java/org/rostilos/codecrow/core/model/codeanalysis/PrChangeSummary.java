package org.rostilos.codecrow.core.model.codeanalysis;

import jakarta.persistence.*;
import java.time.OffsetDateTime;

/** Durable scheduler output, scoped to the owning analysis and project. */
@Entity
@Table(name = "pr_change_summary")
public class PrChangeSummary {
    @Id
    @Column(name = "analysis_id")
    private Long analysisId;
    @Column(name = "project_id", nullable = false)
    private Long projectId;
    @Column(name = "pr_number", nullable = false)
    private Long prNumber;
    @Column(name = "source_revision", nullable = false, length = 64)
    private String sourceRevision;
    @Column(name = "payload", nullable = false, columnDefinition = "TEXT")
    private String payload;
    @Column(name = "created_at", nullable = false)
    private OffsetDateTime createdAt = OffsetDateTime.now();

    protected PrChangeSummary() {}

    public PrChangeSummary(CodeAnalysis analysis, String payload) {
        this.analysisId = analysis.getId();
        this.projectId = analysis.getProject().getId();
        this.prNumber = analysis.getPrNumber();
        this.sourceRevision = analysis.getCommitHash();
        this.payload = payload;
    }

    public Long getAnalysisId() { return analysisId; }
    public Long getProjectId() { return projectId; }
    public Long getPrNumber() { return prNumber; }
    public String getSourceRevision() { return sourceRevision; }
    public String getPayload() { return payload; }
}
