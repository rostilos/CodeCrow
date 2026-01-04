package org.rostilos.codecrow.webserver.analysis.dto.response;

import org.rostilos.codecrow.core.dto.analysis.issue.IssueDTO;
import org.rostilos.codecrow.core.dto.analysis.issue.IssuesSummaryDTO;

import java.util.ArrayList;
import java.util.List;

public class AnalysisIssueResponse {
    private List<IssueDTO> issues = new ArrayList<>();
    private IssuesSummaryDTO summary = new IssuesSummaryDTO(0, 0, 0, 0, 0, 0, 0, 0, 0, 0, 0, 0, 0, 0);
    private int maxVersion;
    private int currentVersion;
    private String analysisSummary; // The comment/summary from the CodeAnalysis
    private String commitHash; // The commit hash for this specific analysis version

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

    public int getCurrentVersion() {
        return currentVersion;
    }
    public void setCurrentVersion(int currentVersion) {
        this.currentVersion = currentVersion;
    }

    public String getAnalysisSummary() {
        return analysisSummary;
    }
    public void setAnalysisSummary(String analysisSummary) {
        this.analysisSummary = analysisSummary;
    }

    public String getCommitHash() {
        return commitHash;
    }
    public void setCommitHash(String commitHash) {
        this.commitHash = commitHash;
    }
}
