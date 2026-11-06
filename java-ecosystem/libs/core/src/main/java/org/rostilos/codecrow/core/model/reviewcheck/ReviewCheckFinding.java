package org.rostilos.codecrow.core.model.reviewcheck;

import jakarta.persistence.*;
import org.rostilos.codecrow.core.model.codeanalysis.AnalysisFinding;

import java.nio.charset.StandardCharsets;
import java.security.MessageDigest;
import java.security.NoSuchAlgorithmException;
import java.time.OffsetDateTime;
import java.util.HexFormat;

@Entity
@Table(name = "review_check_finding", uniqueConstraints = @UniqueConstraint(columnNames = {"result_id", "finding_key"}))
public class ReviewCheckFinding extends AnalysisFinding {
    @Id @GeneratedValue(strategy = GenerationType.IDENTITY)
    private Long id;

    @org.hibernate.annotations.OnDelete(action = org.hibernate.annotations.OnDeleteAction.CASCADE)
    @ManyToOne(fetch = FetchType.LAZY, optional = false)
    @JoinColumn(name = "result_id", nullable = false)
    private ReviewCheckResult result;

    @Column(name = "finding_key", nullable = false, length = 64)
    private String findingKey;

    @Column(name = "manual_status_resolved")
    private Boolean manualStatusResolved;

    @Column(name = "manual_status_updated_at")
    private OffsetDateTime manualStatusUpdatedAt;

    @Column(name = "resolved_description", columnDefinition = "TEXT")
    private String resolvedDescription;

    public Long getId() { return id; }
    public ReviewCheckResult getResult() { return result; }
    public void setResult(ReviewCheckResult result) { this.result = result; }
    public String getFindingKey() { return findingKey; }
    public void setFindingKey(String findingKey) { this.findingKey = findingKey; }
    public Boolean getManualStatusResolved() { return manualStatusResolved; }
    public void setManualStatusResolved(Boolean value) { manualStatusResolved = value; }
    public OffsetDateTime getManualStatusUpdatedAt() { return manualStatusUpdatedAt; }
    public void setManualStatusUpdatedAt(OffsetDateTime value) { manualStatusUpdatedAt = value; }
    public String getResolvedDescription() { return resolvedDescription; }
    public void setResolvedDescription(String value) { resolvedDescription = value; }

    public static String key(String identity) {
        try {
            return HexFormat.of().formatHex(MessageDigest.getInstance("SHA-256").digest(identity.getBytes(StandardCharsets.UTF_8)));
        } catch (NoSuchAlgorithmException impossible) { throw new IllegalStateException(impossible); }
    }

    public void inheritDecision(ReviewCheckFinding previous) {
        if (previous.manualStatusResolved == null) return;
        manualStatusResolved = previous.manualStatusResolved;
        manualStatusUpdatedAt = previous.manualStatusUpdatedAt;
        resolvedDescription = previous.resolvedDescription;
        resolved = manualStatusResolved;
    }

    public ReviewCheckFinding copy(boolean preserveDecision) {
        var copy = new ReviewCheckFinding();
        copy.findingKey = findingKey; copy.severity = severity; copy.filePath = filePath;
        copy.lineNumber = lineNumber; copy.reason = reason; copy.title = title;
        copy.suggestedFixDescription = suggestedFixDescription; copy.issueCategory = issueCategory;
        copy.codeSnippet = codeSnippet; copy.resolved = resolved;
        if (preserveDecision) copy.inheritDecision(this);
        else if (Boolean.TRUE.equals(manualStatusResolved)) copy.resolved = false;
        return copy;
    }
}
