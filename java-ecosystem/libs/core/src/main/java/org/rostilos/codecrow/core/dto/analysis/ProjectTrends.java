package org.rostilos.codecrow.core.dto.analysis;

public class ProjectTrends {
    private int issuesResolvedLast7Days;
    private int newIssuesLast7Days;
    private String averageResolutionTime;

    public ProjectTrends() {
        // ...
    }

    public int getIssuesResolvedLast7Days() {
        return issuesResolvedLast7Days;
    }

    public void setIssuesResolvedLast7Days(int issuesResolvedLast7Days) {
        this.issuesResolvedLast7Days = issuesResolvedLast7Days;
    }

    public int getNewIssuesLast7Days() {
        return newIssuesLast7Days;
    }

    public void setNewIssuesLast7Days(int newIssuesLast7Days) {
        this.newIssuesLast7Days = newIssuesLast7Days;
    }

    public String getAverageResolutionTime() {
        return averageResolutionTime;
    }

    public void setAverageResolutionTime(String averageResolutionTime) {
        this.averageResolutionTime = averageResolutionTime;
    }
}
