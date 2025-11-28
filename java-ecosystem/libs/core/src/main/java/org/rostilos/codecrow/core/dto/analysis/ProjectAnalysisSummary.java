package org.rostilos.codecrow.core.dto.analysis;

import java.time.OffsetDateTime;
import java.util.ArrayList;
import java.util.List;

public class ProjectAnalysisSummary {
    private String projectName;
    private OffsetDateTime lastAnalysisAt;
    private int totalIssues;
    private int criticalIssues;
    private int totalPullRequests;
    private int openPullRequests;
    private List<BranchSummary> branches = new ArrayList<>();
    private ProjectTrends trends = new ProjectTrends();

    public ProjectAnalysisSummary() {
        // ...
    }

    public String getProjectName() {
        return projectName;
    }

    public void setProjectName(String projectName) {
        this.projectName = projectName;
    }

    public OffsetDateTime getLastAnalysisAt() {
        return lastAnalysisAt;
    }

    public void setLastAnalysisAt(OffsetDateTime lastAnalysisAt) {
        this.lastAnalysisAt = lastAnalysisAt;
    }

    public int getTotalIssues() {
        return totalIssues;
    }

    public void setTotalIssues(int totalIssues) {
        this.totalIssues = totalIssues;
    }

    public int getCriticalIssues() {
        return criticalIssues;
    }

    public void setCriticalIssues(int criticalIssues) {
        this.criticalIssues = criticalIssues;
    }

    public int getTotalPullRequests() {
        return totalPullRequests;
    }

    public void setTotalPullRequests(int totalPullRequests) {
        this.totalPullRequests = totalPullRequests;
    }

    public int getOpenPullRequests() {
        return openPullRequests;
    }

    public void setOpenPullRequests(int openPullRequests) {
        this.openPullRequests = openPullRequests;
    }

    public List<BranchSummary> getBranches() {
        return branches;
    }

    public void setBranches(List<BranchSummary> branches) {
        this.branches = branches;
    }

    public ProjectTrends getTrends() {
        return trends;
    }

    public void setTrends(ProjectTrends trends) {
        this.trends = trends;
    }
}
