package org.rostilos.codecrow.webserver.dto.response.analysis;

import org.rostilos.codecrow.core.dto.analysis.issue.IssueDTO;
import org.rostilos.codecrow.core.dto.analysis.issue.IssuesSummaryDTO;

import java.util.ArrayList;
import java.util.List;

public class AnalysisIssueResponse {
    private List<IssueDTO> issues = new ArrayList<>();
    private IssuesSummaryDTO summary = new IssuesSummaryDTO(0, 0, 0, 0, 0, 0, 0, 0, 0, 0, 0, 0, 0, 0);
    private int maxVersion;

    public AnalysisIssueResponse() {
    }

    public List<IssueDTO> getIssues() {
        return issues;
    }
    public void setIssues(List<IssueDTO> issues) {
        this.issues = issues;
    }

    public IssuesSummaryDTO getSummary() {
        return summary;
    }
    public void setSummary(IssuesSummaryDTO summary) {
        this.summary = summary;
    }

    public int getMaxVersion() {
        return maxVersion;
    }
    public void setMaxVersion(int maxVersion) {
        this.maxVersion = maxVersion;
    }
}
