package org.rostilos.codecrow.core.model.reviewcheck;

import jakarta.persistence.*;
import org.hibernate.annotations.JdbcTypeCode;
import org.hibernate.type.SqlTypes;
import org.rostilos.codecrow.core.model.codeanalysis.CodeAnalysis;

import java.util.ArrayList;
import java.util.List;

@Entity
@Table(name = "review_check_result", uniqueConstraints = @UniqueConstraint(columnNames = {"analysis_id", "check_id"}))
public class ReviewCheckResult {
    @Id @GeneratedValue(strategy = GenerationType.IDENTITY)
    private Long id;

    @org.hibernate.annotations.OnDelete(action = org.hibernate.annotations.OnDeleteAction.CASCADE)
    @ManyToOne(fetch = FetchType.LAZY, optional = false)
    @JoinColumn(name = "analysis_id", nullable = false)
    private CodeAnalysis analysis;

    @Column(name = "check_id", nullable = false, length = 128)
    private String checkId;

    @Column(nullable = false)
    private boolean complete;

    @JdbcTypeCode(SqlTypes.JSON)
    @Column(nullable = false, columnDefinition = "jsonb")
    private List<String> diagnostics = new ArrayList<>();

    @OneToMany(mappedBy = "result", cascade = CascadeType.ALL, orphanRemoval = true)
    @OrderBy("id")
    private List<ReviewCheckFinding> findings = new ArrayList<>();

    public Long getId() { return id; }
    public CodeAnalysis getAnalysis() { return analysis; }
    public void setAnalysis(CodeAnalysis analysis) { this.analysis = analysis; }
    public String getCheckId() { return checkId; }
    public void setCheckId(String checkId) { this.checkId = checkId; }
    public boolean isComplete() { return complete; }
    public void setComplete(boolean complete) { this.complete = complete; }
    public List<String> getDiagnostics() { return diagnostics; }
    public void setDiagnostics(List<String> diagnostics) { this.diagnostics = new ArrayList<>(diagnostics); }
    public List<ReviewCheckFinding> getFindings() { return findings; }
    public void addFinding(ReviewCheckFinding finding) { finding.setResult(this); findings.add(finding); }

    public ReviewCheckResult copy(boolean preserveDecisions) {
        var copy = new ReviewCheckResult();
        copy.setCheckId(checkId); copy.setComplete(complete); copy.setDiagnostics(diagnostics);
        findings.forEach(finding -> copy.addFinding(finding.copy(preserveDecisions)));
        return copy;
    }
}
