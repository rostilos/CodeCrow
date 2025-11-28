package org.rostilos.codecrow.core.dto.analysis;

import java.time.OffsetDateTime;

public class BranchSummary {
    private String name;
    private OffsetDateTime lastAnalysisAt;
    private int issueCount;
    private int criticalIssueCount;

    public BranchSummary() {
        // ...
    }

    public String getName() {
        return name;
    }

    public void setName(String name) {
        this.name = name;
    }

    public OffsetDateTime getLastAnalysisAt() {
        return lastAnalysisAt;
    }

    public void setLastAnalysisAt(OffsetDateTime lastAnalysisAt) {
        this.lastAnalysisAt = lastAnalysisAt;
    }

    public int getIssueCount() {
        return issueCount;
    }

    public void setIssueCount(int issueCount) {
        this.issueCount = issueCount;
    }

    public int getCriticalIssueCount() {
        return criticalIssueCount;
    }

    public void setCriticalIssueCount(int criticalIssueCount) {
        this.criticalIssueCount = criticalIssueCount;
    }
}
