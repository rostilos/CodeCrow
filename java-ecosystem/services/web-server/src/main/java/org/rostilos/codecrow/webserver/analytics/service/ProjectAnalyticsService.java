package org.rostilos.codecrow.webserver.analytics.service;

import org.rostilos.codecrow.core.model.branch.Branch;
import org.rostilos.codecrow.core.model.branch.BranchIssue;
import org.rostilos.codecrow.core.model.codeanalysis.CodeAnalysis;
import org.rostilos.codecrow.core.service.BranchService;
import org.rostilos.codecrow.core.service.CodeAnalysisService;
import org.rostilos.codecrow.webserver.analysis.service.AnalysisService;
import org.rostilos.codecrow.webserver.project.service.ProjectService;
import org.slf4j.Logger;
import org.slf4j.LoggerFactory;
import org.springframework.stereotype.Service;
import org.springframework.transaction.annotation.Transactional;

import java.util.ArrayList;
import java.util.Comparator;
import java.util.List;
import java.util.Optional;

@Service
@Transactional
public class ProjectAnalyticsService {
    private static final Logger log = LoggerFactory.getLogger(AnalysisService.class);

    private final CodeAnalysisService codeAnalysisService;
    private final BranchService branchService;
    private final ProjectService projectService;

    public ProjectAnalyticsService(
            CodeAnalysisService codeAnalysisService,
            BranchService branchService,
            ProjectService projectService
    ) {
        this.codeAnalysisService = codeAnalysisService;
        this.branchService = branchService;
        this.projectService = projectService;
    }

    /**
     * Return total issues trend for a specific branch.
     * The trend is a list of points with timestamp and total issues count from branch analysis history.
     * - branch is required for this method
     * - limit controls how many recent analyses are returned (use 0 or negative for all)
     * - timeframeDays controls the date range to include (default 30 days)
     */
    public List<BranchIssuesTrendPoint> getBranchIssuesTrend(Long projectId, String branch, int limit, int timeframeDays) {
        if (branch == null || branch.isBlank()) {
            return new ArrayList<>();
        }

        List<CodeAnalysis> analyses = getBranchAnalysisHistory(projectId, branch);

        // Filter by timeframe if specified
        if (timeframeDays > 0) {
            java.time.OffsetDateTime cutoff = java.time.OffsetDateTime.now().minusDays(timeframeDays);
            analyses = analyses.stream()
                    .filter(a -> a.getCreatedAt() != null && a.getCreatedAt().isAfter(cutoff))
                    .toList();
        }

        // order by createdAt ascending (older -> newer) to produce trend
        analyses = analyses.stream()
                .sorted(Comparator.comparing(CodeAnalysis::getCreatedAt, Comparator.nullsFirst(Comparator.naturalOrder())))
                .toList();

        // if limit provided, take last 'limit' items
        if (limit > 0 && analyses.size() > limit) {
            analyses = analyses.subList(Math.max(0, analyses.size() - limit), analyses.size());
        }

        List<BranchIssuesTrendPoint> trend = new ArrayList<>();
        for (CodeAnalysis a : analyses) {
            String date = a.getCreatedAt() == null ? a.getUpdatedAt().toString() : a.getCreatedAt().toString();
            trend.add(new BranchIssuesTrendPoint(
                    date,
                    a.getTotalIssues(),
                    a.getHighSeverityCount(),
                    a.getMediumSeverityCount(),
                    a.getLowSeverityCount()
            ));
        }
        return trend;
    }

    public List<CodeAnalysis> getBranchAnalysisHistory(Long projectId, String branchName) {
        return branchService.getBranchAnalysisHistory(projectId, branchName);
    }

    public BranchService.BranchStats getBranchStats(Long projectId, String branchName) {
        return branchService.getBranchStats(projectId, branchName);
    }

    public List<CodeAnalysis> getAnalysisHistory(Long projectId, String branch) {
        List<CodeAnalysis> analyses = codeAnalysisService.findByProjectId(projectId);
        if (branch != null && !branch.isBlank()) {
            analyses = analyses.stream()
                    .filter(a -> branch.equals(a.getBranchName()))
                    .toList();
        }
        return analyses;
    }

    public List<CodeAnalysis> getAnalysisHistory(Long workspaceId, String namespace, String branch) {
        Long projectId = projectService.getProjectByWorkspaceAndNamespace(workspaceId, namespace).getId();
        return getAnalysisHistory(projectId, branch);
    }

    /**
     * Calculate issue trend direction based on historical data within the timeframe.
     * Returns "up" if issues are increasing, "down" if decreasing, "stable" if no significant change.
     *
     * @param projectId the project ID
     * @param branch optional branch filter
     * @param timeframeDays number of days to look back (default 30)
     * @return "up", "down", or "stable"
     */
    public String calculateTrend(Long projectId, String branch, int timeframeDays) {
        List<CodeAnalysis> analyses = getAnalysisHistory(projectId, branch);

        if (analyses.isEmpty()) {
            return "stable";
        }

        // Filter analyses within timeframe
        java.time.OffsetDateTime cutoff = java.time.OffsetDateTime.now().minusDays(timeframeDays);
        List<CodeAnalysis> recentAnalyses = analyses.stream()
                .filter(a -> a.getCreatedAt() != null && a.getCreatedAt().isAfter(cutoff))
                .sorted(Comparator.comparing(CodeAnalysis::getCreatedAt))
                .toList();

        if (recentAnalyses.size() < 2) {
            return "stable";
        }

        // Calculate average issues in first half vs second half
        int midpoint = recentAnalyses.size() / 2;
        List<CodeAnalysis> firstHalf = recentAnalyses.subList(0, midpoint);
        List<CodeAnalysis> secondHalf = recentAnalyses.subList(midpoint, recentAnalyses.size());

        double firstHalfAvg = firstHalf.stream()
                .mapToInt(CodeAnalysis::getTotalIssues)
                .average()
                .orElse(0.0);

        double secondHalfAvg = secondHalf.stream()
                .mapToInt(CodeAnalysis::getTotalIssues)
                .average()
                .orElse(0.0);

        // Calculate percentage change
        double changeThreshold = 0.1; // 10% change threshold
        if (firstHalfAvg == 0) {
            return secondHalfAvg > 0 ? "up" : "stable";
        }

        double percentChange = (secondHalfAvg - firstHalfAvg) / firstHalfAvg;

        if (percentChange > changeThreshold) {
            return "up";
        } else if (percentChange < -changeThreshold) {
            return "down";
        } else {
            return "stable";
        }
    }

    public List<BranchIssue> getBranchIssues(Long projectId, String branchName) {
        Optional<Branch> branchOpt = branchService.findByProjectIdAndBranchName(projectId, branchName);
        return branchOpt.map(branch -> branchService.findIssuesByBranchId(branch.getId())).orElse(new ArrayList<>());
    }

    public CodeAnalysisService.AnalysisStats getProjectStats(Long projectId) {
        return codeAnalysisService.getProjectAnalysisStats(projectId);
    }

    public Optional<CodeAnalysis> findLatestAnalysis(Long projectId) {
        return codeAnalysisService.findLatestByProjectId(projectId);
    }

    /**
     * Return resolved-issues trend for a project.
     * The trend is a list of points with timestamp, resolvedCount, totalIssues and resolvedRate (0.0-1.0).
     * - branch may be null to include all branches
     * - limit controls how many recent analyses are returned (use 0 or negative for all)
     * - timeframeDays controls the date range to include (default 30 days)
     */
    public List<ResolvedTrendPoint> getResolvedTrend(Long projectId, String branch, int limit, int timeframeDays) {
        List<CodeAnalysis> analyses = getAnalysisHistory(projectId, branch);

        // Filter by timeframe if specified
        if (timeframeDays > 0) {
            java.time.OffsetDateTime cutoff = java.time.OffsetDateTime.now().minusDays(timeframeDays);
            analyses = analyses.stream()
                    .filter(a -> a.getCreatedAt() != null && a.getCreatedAt().isAfter(cutoff))
                    .toList();
        }

        // order by createdAt ascending (older -> newer) to produce trend
        analyses = analyses.stream()
                .sorted(Comparator.comparing(CodeAnalysis::getCreatedAt, Comparator.nullsFirst(Comparator.naturalOrder())))
                .toList();

        // if limit provided, take last 'limit' items
        if (limit > 0 && analyses.size() > limit) {
            analyses = analyses.subList(Math.max(0, analyses.size() - limit), analyses.size());
        }

        List<ResolvedTrendPoint> trend = new ArrayList<>();
        for (CodeAnalysis a : analyses) {
            int total = a.getTotalIssues();
            int resolved = a.getResolvedCount();
            double rate = 0.0;
            if (total > 0) {
                rate = (double) resolved / (double) total;
            }
            String date = a.getCreatedAt() == null ? a.getUpdatedAt().toString() : a.getCreatedAt().toString();
            trend.add(new ResolvedTrendPoint(date, resolved, total, rate));
        }
        return trend;
    }

    /**
     * Simple DTO representing a single point in the resolved issues trend.
     */
    public static class ResolvedTrendPoint {
        private String date;
        private int resolvedCount;
        private int totalIssues;
        private double resolvedRate;

        public ResolvedTrendPoint() {}

        public ResolvedTrendPoint(String date, int resolvedCount, int totalIssues, double resolvedRate) {
            this.date = date;
            this.resolvedCount = resolvedCount;
            this.totalIssues = totalIssues;
            this.resolvedRate = resolvedRate;
        }

        public String getDate() { return date; }
        public void setDate(String date) { this.date = date; }

        public int getResolvedCount() { return resolvedCount; }
        public void setResolvedCount(int resolvedCount) { this.resolvedCount = resolvedCount; }

        public int getTotalIssues() { return totalIssues; }
        public void setTotalIssues(int totalIssues) { this.totalIssues = totalIssues; }

        public double getResolvedRate() { return resolvedRate; }
        public void setResolvedRate(double resolvedRate) { this.resolvedRate = resolvedRate; }
    }

    /**
     * DTO representing a single point in the branch issues trend.
     */
    public static class BranchIssuesTrendPoint {
        private String date;
        private int totalIssues;
        private int highSeverityCount;
        private int mediumSeverityCount;
        private int lowSeverityCount;

        public BranchIssuesTrendPoint() {
        }

        public BranchIssuesTrendPoint(String date, int totalIssues, int highSeverityCount, int mediumSeverityCount, int lowSeverityCount) {
            this.date = date;
            this.totalIssues = totalIssues;
            this.highSeverityCount = highSeverityCount;
            this.mediumSeverityCount = mediumSeverityCount;
            this.lowSeverityCount = lowSeverityCount;
        }

        public String getDate() {
            return date;
        }

        public void setDate(String date) {
            this.date = date;
        }

        public int getTotalIssues() {
            return totalIssues;
        }

        public void setTotalIssues(int totalIssues) {
            this.totalIssues = totalIssues;
        }

        public int getHighSeverityCount() {
            return highSeverityCount;
        }

        public void setHighSeverityCount(int highSeverityCount) {
            this.highSeverityCount = highSeverityCount;
        }

        public int getMediumSeverityCount() {
            return mediumSeverityCount;
        }

        public void setMediumSeverityCount(int mediumSeverityCount) {
            this.mediumSeverityCount = mediumSeverityCount;
        }

        public int getLowSeverityCount() {
            return lowSeverityCount;
        }

        public void setLowSeverityCount(int lowSeverityCount) {
            this.lowSeverityCount = lowSeverityCount;
        }
    }
}
