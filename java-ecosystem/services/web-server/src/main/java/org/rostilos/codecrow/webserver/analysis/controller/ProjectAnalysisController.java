package org.rostilos.codecrow.webserver.analysis.controller;

import org.rostilos.codecrow.core.dto.analysis.AnalysisItemDTO;
import org.rostilos.codecrow.core.model.codeanalysis.CodeAnalysis;
import org.rostilos.codecrow.core.model.codeanalysis.CodeAnalysisIssue;
import org.rostilos.codecrow.core.model.codeanalysis.IssueSeverity;
import org.rostilos.codecrow.webserver.analysis.dto.response.AnalysesHistoryResponse;
import org.rostilos.codecrow.webserver.analysis.service.AnalysisService;
import org.rostilos.codecrow.webserver.project.service.ProjectService;
import org.rostilos.codecrow.core.model.project.Project;
import org.rostilos.codecrow.core.service.CodeAnalysisService;
import org.rostilos.codecrow.webserver.workspace.service.WorkspaceService;
import org.springframework.http.ResponseEntity;
import org.springframework.security.access.prepost.PreAuthorize;
import org.springframework.web.bind.annotation.*;

import java.time.OffsetDateTime;
import java.util.*;

@CrossOrigin(origins = "*", maxAge = 3600)
@RestController
@RequestMapping("/api/{workspaceSlug}/projects/{projectNamespace}/analysis")
public class ProjectAnalysisController {

    private final AnalysisService analysisService;
    private final ProjectService projectService;
    private final WorkspaceService workspaceService;

    public ProjectAnalysisController(AnalysisService analysisService, ProjectService projectService, WorkspaceService workspaceService) {
        this.analysisService = analysisService;
        this.projectService = projectService;
        this.workspaceService = workspaceService;
    }

    /**
     * GET /api/{workspaceId}/projects/{projectId}/analysis/summary
     * Query params: ?branch={branch}
     * Response:
     * {
     *   totalIssues: number,
     *   criticalIssues: number,
     *   highIssues: number,
     *   mediumIssues: number,
     *   lowIssues: number,
     *   lastAnalysisDate?: string
     * }
     */
    @GetMapping("/summary")
    @PreAuthorize("@workspaceSecurity.isWorkspaceMember(#workspaceSlug, authentication)")
    public ResponseEntity<ProjectSummaryResponse> getProjectSummary(
            @PathVariable String workspaceSlug,
            @PathVariable String projectNamespace,
            @RequestParam(name = "branch", required = false) String branch
    ) {
        Long workspaceId = workspaceService.getWorkspaceBySlug(workspaceSlug).getId();
        Project project = projectService.getProjectByWorkspaceAndNamespace(workspaceId, projectNamespace);
        Long projectId = project.getId();

        ProjectSummaryResponse resp = new ProjectSummaryResponse();

        if (branch != null && !branch.isBlank()) {
            org.rostilos.codecrow.core.service.BranchService.BranchStats stats = analysisService.getBranchStats(projectId, branch);
            resp.setTotalIssues((int) stats.getTotalIssues());
            resp.setCriticalIssues((int) stats.getHighSeverityCount());
            resp.setHighIssues((int) stats.getHighSeverityCount());
            resp.setMediumIssues((int) stats.getMediumSeverityCount());
            resp.setLowIssues((int) stats.getLowSeverityCount());
            if (stats.getLastAnalysisDate() != null) {
                resp.setLastAnalysisDate(stats.getLastAnalysisDate().toString());
            }
        } else {
            CodeAnalysisService.AnalysisStats stats = analysisService.getProjectStats(projectId);
            resp.setTotalIssues((int) stats.getTotalIssues());
            resp.setCriticalIssues((int) stats.getHighSeverityCount());
            resp.setHighIssues((int) stats.getHighSeverityCount());
            resp.setMediumIssues((int) stats.getMediumSeverityCount());
            resp.setLowIssues((int) stats.getLowSeverityCount());
            Optional<CodeAnalysis> latest = analysisService.findLatestAnalysis(projectId);
            latest.ifPresent(a -> resp.setLastAnalysisDate(a.getUpdatedAt() == null ? null : a.getUpdatedAt().toString()));
        }

        return ResponseEntity.ok(resp);
    }

    /**
     * GET /api/{workspaceId}/projects/{projectId}/analysis/detailed-stats
     * Query params: ?branch={branch}
     * Response shape described in the task — this method builds that shape using available services.
     */
    @GetMapping("/detailed-stats")
    @PreAuthorize("@workspaceSecurity.isWorkspaceMember(#workspaceSlug, authentication)")
    public ResponseEntity<DetailedStatsResponse> getDetailedStats(
            @PathVariable String workspaceSlug,
            @PathVariable String projectNamespace,
            @RequestParam(name = "branch", required = false) String branch,
            @RequestParam(name = "timeframeDays", required = false, defaultValue = "30") int timeframeDays
    ) {
        Long workspaceId = workspaceService.getWorkspaceBySlug(workspaceSlug).getId();
        Project project = projectService.getProjectByWorkspaceAndNamespace(workspaceId, projectNamespace);
        Long projectId = project.getId();

        DetailedStatsResponse resp = new DetailedStatsResponse();

        //TODO: service method to avoid code duplication
        if (branch != null && !branch.isBlank()) {
            org.rostilos.codecrow.core.service.BranchService.BranchStats stats = analysisService.getBranchStats(projectId, branch);
            List<org.rostilos.codecrow.core.model.branch.BranchIssue> branchIssues = analysisService.getBranchIssues(projectId, branch);
            List<CodeAnalysis> history = analysisService.getBranchAnalysisHistory(projectId, branch);

            resp.setTotalIssues((int) stats.getTotalIssues());
            resp.setCriticalIssues((int) stats.getHighSeverityCount());
            resp.setHighIssues((int) stats.getHighSeverityCount());
            resp.setMediumIssues((int) stats.getMediumSeverityCount());
            resp.setLowIssues((int) stats.getLowSeverityCount());

            if (stats.getLastAnalysisDate() != null) {
                resp.setLastAnalysisDate(stats.getLastAnalysisDate().toString());
            }

            String trend = analysisService.calculateTrend(projectId, branch, timeframeDays);
            resp.setTrend(trend);

            Map<String, Integer> issuesByType = new HashMap<>();
            issuesByType.put("security", 0);
            issuesByType.put("quality", 0);
            issuesByType.put("performance", 0);
            issuesByType.put("style", 0);
            issuesByType.put("bug_risk", 0);
            issuesByType.put("documentation", 0);
            issuesByType.put("best_practices", 0);
            issuesByType.put("error_handling", 0);
            issuesByType.put("testing", 0);
            issuesByType.put("architecture", 0);

            for (org.rostilos.codecrow.core.model.branch.BranchIssue bi : branchIssues) {
                if (bi.isResolved()) continue;
                CodeAnalysisIssue cai = bi.getCodeAnalysisIssue();
                if (cai.getIssueCategory() == null) continue;
                String catKey = cai.getIssueCategory().name().toLowerCase();
                if (catKey.equals("code_quality")) catKey = "quality";
                issuesByType.merge(catKey, 1, Integer::sum);
            }
            resp.setIssuesByType(issuesByType);

            List<DetailedStatsResponse.RecentAnalysis> recent = history.stream()
                    .limit(10)
                    .map(a -> {
                        DetailedStatsResponse.RecentAnalysis r = new DetailedStatsResponse.RecentAnalysis();
                        r.setDate(a.getCreatedAt() == null ? null : a.getCreatedAt().toString());
                        r.setTotalIssues(a.getTotalIssues());
                        r.setTargetBranch(a.getBranchName());
                        r.setSourceBranch(a.getSourceBranchName());
                        r.setStatus(mapStatus(a.getStatus()));
                        return r;
                    })
                    .toList();
            resp.setRecentAnalyses(recent);

            List<DetailedStatsResponse.TopFile> topFiles = new ArrayList<>();
            List<Object[]> problematic = stats.getMostProblematicFiles();
            if (problematic != null) {
                for (Object[] row : problematic) {
                    if (row == null || row.length < 2) continue;
                    String file = row[0] == null ? "unknown" : String.valueOf(row[0]);
                    int cnt = 0;
                    try {
                        cnt = row[1] instanceof Number ? ((Number) row[1]).intValue() : Integer.parseInt(String.valueOf(row[1]));
                    } catch (Exception ignored) {}
                    DetailedStatsResponse.TopFile tf = new DetailedStatsResponse.TopFile();
                    tf.setFile(file);
                    tf.setIssues(cnt);

                    Optional<org.rostilos.codecrow.core.model.codeanalysis.IssueSeverity> max = branchIssues.stream()
                            .filter(bi -> !bi.isResolved())
                            .filter(bi -> file.equals(bi.getCodeAnalysisIssue().getFilePath()))
                            .map(org.rostilos.codecrow.core.model.branch.BranchIssue::getSeverity)
                            .filter(Objects::nonNull)
                            .sorted(Comparator.comparingInt(ProjectAnalysisController::severityRank))
                            .findFirst();
                    String sev = "medium";
                    if (max.isPresent()) {
                        org.rostilos.codecrow.core.model.codeanalysis.IssueSeverity s = max.get();
                        if (s == org.rostilos.codecrow.core.model.codeanalysis.IssueSeverity.HIGH) sev = "critical";
                        else if (s == org.rostilos.codecrow.core.model.codeanalysis.IssueSeverity.MEDIUM) sev = "medium";
                        else if (s == org.rostilos.codecrow.core.model.codeanalysis.IssueSeverity.LOW) sev = "low";
                    }
                    tf.setSeverity(sev);
                    topFiles.add(tf);
                }
            }
            resp.setTopFiles(topFiles);

            Map<String, DetailedStatsResponse.BranchStat> branchMap = new HashMap<>();
            DetailedStatsResponse.BranchStat bs = new DetailedStatsResponse.BranchStat();
            bs.setTotalIssues((int) stats.getTotalIssues());
            bs.setLastScan(stats.getLastAnalysisDate() == null ? null : stats.getLastAnalysisDate().toString());
            branchMap.put(branch, bs);
            resp.setBranchStats(branchMap);

        } else {
            CodeAnalysisService.AnalysisStats stats = analysisService.getProjectStats(projectId);
            List<CodeAnalysisIssue> allIssues = analysisService.findIssues(projectId, null, null, null, null, 0);
            List<CodeAnalysis> history = analysisService.getAnalysisHistory(projectId, null);

            resp.setTotalIssues((int) stats.getTotalIssues());
            resp.setCriticalIssues((int) stats.getHighSeverityCount());
            resp.setHighIssues((int) stats.getHighSeverityCount());
            resp.setMediumIssues((int) stats.getMediumSeverityCount());
            resp.setLowIssues((int) stats.getLowSeverityCount());

            if (!history.isEmpty()) {
                CodeAnalysis latest = history.get(0);
                resp.setLastAnalysisDate(latest.getUpdatedAt() == null ? null : latest.getUpdatedAt().toString());
            }

            String trend = analysisService.calculateTrend(projectId, null, timeframeDays);
            resp.setTrend(trend);

            Map<String, Integer> issuesByType = new HashMap<>();
            issuesByType.put("security", 0);
            issuesByType.put("quality", 0);
            issuesByType.put("performance", 0);
            issuesByType.put("style", 0);
            issuesByType.put("bug_risk", 0);
            issuesByType.put("documentation", 0);
            issuesByType.put("best_practices", 0);
            issuesByType.put("error_handling", 0);
            issuesByType.put("testing", 0);
            issuesByType.put("architecture", 0);

            for (CodeAnalysisIssue i : allIssues) {
                if (i.getIssueCategory() == null) continue;
                String catKey = i.getIssueCategory().name().toLowerCase();
                if (catKey.equals("code_quality")) catKey = "quality";
                issuesByType.merge(catKey, 1, Integer::sum);
            }
            resp.setIssuesByType(issuesByType);

            List<DetailedStatsResponse.RecentAnalysis> recent = history.stream()
                    .limit(10)
                    .map(a -> {
                        DetailedStatsResponse.RecentAnalysis r = new DetailedStatsResponse.RecentAnalysis();
                        r.setDate(a.getCreatedAt() == null ? null : a.getCreatedAt().toString());
                        r.setTotalIssues(a.getTotalIssues());
                        r.setTargetBranch(a.getBranchName());
                        r.setSourceBranch(a.getSourceBranchName());
                        r.setStatus(mapStatus(a.getStatus()));
                        return r;
                    })
                    .toList();
            resp.setRecentAnalyses(recent);

            List<DetailedStatsResponse.TopFile> topFiles = new ArrayList<>();
            List<Object[]> problematic = stats.getMostProblematicFiles();
            if (problematic != null) {
                for (Object[] row : problematic) {
                    if (row == null || row.length < 2) continue;
                    String file = row[0] == null ? "unknown" : String.valueOf(row[0]);
                    int cnt = 0;
                    try {
                        cnt = row[1] instanceof Number ? ((Number) row[1]).intValue() : Integer.parseInt(String.valueOf(row[1]));
                    } catch (Exception ignored) {}
                    DetailedStatsResponse.TopFile tf = new DetailedStatsResponse.TopFile();
                    tf.setFile(file);
                    tf.setIssues(cnt);

                    Optional<IssueSeverity> max = allIssues.stream()
                            .filter(i -> file.equals(i.getFilePath()))
                            .map(CodeAnalysisIssue::getSeverity)
                            .filter(Objects::nonNull)
                            .sorted(Comparator.comparingInt(ProjectAnalysisController::severityRank))
                            .findFirst();
                    String sev = "medium";
                    if (max.isPresent()) {
                        IssueSeverity s = max.get();
                        if (s == IssueSeverity.HIGH) sev = "critical";
                        else if (s == IssueSeverity.MEDIUM) sev = "medium";
                        else if (s == IssueSeverity.LOW) sev = "low";
                    }
                    tf.setSeverity(sev);
                    topFiles.add(tf);
                }
            }
            resp.setTopFiles(topFiles);

            Map<String, DetailedStatsResponse.BranchStat> branchMap = new HashMap<>();
            for (CodeAnalysis a : history) {
                String branchName = a.getBranchName() == null ? "unknown" : a.getBranchName();
                DetailedStatsResponse.BranchStat curr = branchMap.get(branchName);
                OffsetDateTime lastScan = a.getUpdatedAt();
                if (curr == null) {
                    DetailedStatsResponse.BranchStat bst = new DetailedStatsResponse.BranchStat();
                    bst.setTotalIssues(a.getTotalIssues());
                    bst.setLastScan(lastScan == null ? null : lastScan.toString());
                    branchMap.put(branchName, bst);
                } else {
                    String existingLast = curr.getLastScan();
                    OffsetDateTime existingLastDt = existingLast == null ? null : OffsetDateTime.parse(existingLast);
                    if (lastScan != null && (existingLastDt == null || lastScan.isAfter(existingLastDt))) {
                        curr.setLastScan(lastScan.toString());
                        curr.setTotalIssues(a.getTotalIssues());
                    }
                }
            }
            resp.setBranchStats(branchMap);
        }

        return ResponseEntity.ok(resp);
    }

    /**
     * GET /{workspaceId}/api/project/{projectId}/analysis/history
     * Query params: ?page={page}&pageSize={size}&branch={branch}
     */
    @GetMapping("/history")
    @PreAuthorize("@workspaceSecurity.isWorkspaceMember(#workspaceSlug, authentication)")
    public ResponseEntity<AnalysesHistoryResponse> getAnalysisHistory(
            @PathVariable String workspaceSlug,
            @PathVariable String projectNamespace,
            @RequestParam(name = "page", defaultValue = "1") int page,
            @RequestParam(name = "pageSize", defaultValue = "20") int pageSize,
            @RequestParam(name = "branch", required = false) String branch
    ) {
        Long workspaceId = workspaceService.getWorkspaceBySlug(workspaceSlug).getId();
        Project project = projectService.getProjectByWorkspaceAndNamespace(workspaceId, projectNamespace);
        List<CodeAnalysis> analyses = analysisService.getAnalysisHistory(project.getId(), branch);

        List<AnalysisItemDTO> items = analyses.stream()
                .map(AnalysisItemDTO::fromEntity)
                .toList();

        AnalysesHistoryResponse resp = new AnalysesHistoryResponse(items);
        return ResponseEntity.ok(resp);
    }

    /**
     * GET /api/{workspaceId}/projects/{projectNamespace}/analysis/trends/resolved
     * Query params: ?branch={branch}&limit={n}&timeframeDays={days}
     * Returns a list of points {date, resolvedCount, totalIssues, resolvedRate}
     */
    @GetMapping("/trends/resolved")
    @PreAuthorize("@workspaceSecurity.isWorkspaceMember(#workspaceSlug, authentication)")
    public ResponseEntity<List<AnalysisService.ResolvedTrendPoint>> getResolvedTrend(
            @PathVariable String workspaceSlug,
            @PathVariable String projectNamespace,
            @RequestParam(name = "branch", required = false) String branch,
            @RequestParam(name = "limit", defaultValue = "0") int limit,
            @RequestParam(name = "timeframeDays", required = false, defaultValue = "30") int timeframeDays
    ) {
        Long workspaceId = workspaceService.getWorkspaceBySlug(workspaceSlug).getId();
        Project project = projectService.getProjectByWorkspaceAndNamespace(workspaceId, projectNamespace);
        List<AnalysisService.ResolvedTrendPoint> trend =
                analysisService.getResolvedTrend(project.getId(), branch, limit, timeframeDays);
        return ResponseEntity.ok(trend);
    }

    /**
     * GET /api/{workspaceId}/projects/{projectNamespace}/analysis/trends/issues
     * Query params: ?branch={branch}&limit={n}&timeframeDays={days}
     * Returns a list of points {date, totalIssues, highSeverityCount, mediumSeverityCount, lowSeverityCount}
     * Note: This endpoint requires a branch parameter and returns branch-specific data
     */
    @GetMapping("/trends/issues")
    @PreAuthorize("@workspaceSecurity.isWorkspaceMember(#workspaceSlug, authentication)")
    public ResponseEntity<List<AnalysisService.BranchIssuesTrendPoint>> getBranchIssuesTrend(
            @PathVariable String workspaceSlug,
            @PathVariable String projectNamespace,
            @RequestParam(name = "branch", required = true) String branch,
            @RequestParam(name = "limit", defaultValue = "0") int limit,
            @RequestParam(name = "timeframeDays", required = false, defaultValue = "30") int timeframeDays
    ) {
        Long workspaceId = workspaceService.getWorkspaceBySlug(workspaceSlug).getId();
        Project project = projectService.getProjectByWorkspaceAndNamespace(workspaceId, projectNamespace);
        List<AnalysisService.BranchIssuesTrendPoint> trend =
                analysisService.getBranchIssuesTrend(project.getId(), branch, limit, timeframeDays);
        return ResponseEntity.ok(trend);
    }

    /**
     * PUT /api/{workspaceSlug}/projects/{projectNamespace}/analysis/issues/{issueId}/status
     * Update a single issue's status
     */
    @PutMapping("/issues/{issueId}/status")
    @PreAuthorize("@workspaceSecurity.hasOwnerOrAdminRights(#workspaceSlug, authentication)")
    public ResponseEntity<Map<String, Object>> updateIssueStatus(
            @PathVariable String workspaceSlug,
            @PathVariable String projectNamespace,
            @PathVariable Long issueId,
            @RequestBody IssueStatusUpdateRequest request
    ) {
        // Validate workspace and project
        var workspace = workspaceService.getWorkspaceBySlug(workspaceSlug);
        Project project = projectService.getProjectByWorkspaceAndNamespace(workspace.getId(), projectNamespace);
        
        if (project == null) {
            return ResponseEntity.notFound().build();
        }
        
        // Validate issue belongs to project
        Optional<CodeAnalysisIssue> optionalIssue = analysisService.findIssueById(issueId);
        if (optionalIssue.isEmpty()) {
            return ResponseEntity.notFound().build();
        }
        
        CodeAnalysisIssue issue = optionalIssue.get();
        if (issue.getAnalysis() == null || 
            issue.getAnalysis().getProject() == null ||
            !issue.getAnalysis().getProject().getId().equals(project.getId())) {
            return ResponseEntity.notFound().build();
        }
        
        boolean updated = analysisService.updateIssueStatus(issueId, request.isResolved(), request.getComment(), null);
        Map<String, Object> response = new HashMap<>();
        response.put("success", updated);
        response.put("issueId", issueId);
        response.put("newStatus", request.isResolved() ? "resolved" : "open");
        return ResponseEntity.ok(response);
    }

    /**
     * PUT /api/{workspaceSlug}/projects/{projectNamespace}/analysis/issues/bulk-status
     * Update multiple issues' status at once
     */
    @PutMapping("/issues/bulk-status")
    @PreAuthorize("@workspaceSecurity.hasOwnerOrAdminRights(#workspaceSlug, authentication)")
    public ResponseEntity<BulkStatusUpdateResponse> bulkUpdateIssueStatus(
            @PathVariable String workspaceSlug,
            @PathVariable String projectNamespace,
            @RequestBody BulkStatusUpdateRequest request
    ) {
        // Validate workspace and project
        var workspace = workspaceService.getWorkspaceBySlug(workspaceSlug);
        Project project = projectService.getProjectByWorkspaceAndNamespace(workspace.getId(), projectNamespace);
        
        if (project == null) {
            return ResponseEntity.notFound().build();
        }
        
        int successCount = 0;
        int failureCount = 0;
        List<Long> failedIds = new ArrayList<>();

        for (Long issueId : request.getIssueIds()) {
            // Validate each issue belongs to project
            Optional<CodeAnalysisIssue> optionalIssue = analysisService.findIssueById(issueId);
            if (optionalIssue.isEmpty()) {
                failureCount++;
                failedIds.add(issueId);
                continue;
            }
            
            CodeAnalysisIssue issue = optionalIssue.get();
            if (issue.getAnalysis() == null || 
                issue.getAnalysis().getProject() == null ||
                !issue.getAnalysis().getProject().getId().equals(project.getId())) {
                failureCount++;
                failedIds.add(issueId);
                continue;
            }
            
            boolean updated = analysisService.updateIssueStatus(issueId, request.isResolved(), request.getComment(), null);
            if (updated) {
                successCount++;
            } else {
                failureCount++;
                failedIds.add(issueId);
            }
        }

        BulkStatusUpdateResponse response = new BulkStatusUpdateResponse();
        response.setSuccessCount(successCount);
        response.setFailureCount(failureCount);
        response.setFailedIds(failedIds);
        response.setNewStatus(request.isResolved() ? "resolved" : "open");
        return ResponseEntity.ok(response);
    }

    private static int severityRank(IssueSeverity s) {
        if (s == IssueSeverity.HIGH) return 1;
        if (s == IssueSeverity.MEDIUM) return 2;
        if (s == IssueSeverity.LOW) return 3;
        return 4;
    }

    private static String mapStatus(org.rostilos.codecrow.core.model.codeanalysis.AnalysisStatus status) {
        if (status == null) return "in_progress";
        switch (status) {
            case ACCEPTED:
            case REJECTED:
                return "completed";
            case ERROR:
                return "failed";
            case PENDING:
            default:
                return "in_progress";
        }
    }

    // --- Response DTOs as static inner classes to avoid additional files in this change ---
    public static class ProjectSummaryResponse {
        private int totalIssues;
        private int criticalIssues;
        private int highIssues;
        private int mediumIssues;
        private int lowIssues;
        private String lastAnalysisDate;

        public int getTotalIssues() { return totalIssues; }
        public void setTotalIssues(int totalIssues) { this.totalIssues = totalIssues; }

        public int getCriticalIssues() { return criticalIssues; }
        public void setCriticalIssues(int criticalIssues) { this.criticalIssues = criticalIssues; }

        public int getHighIssues() { return highIssues; }
        public void setHighIssues(int highIssues) { this.highIssues = highIssues; }

        public int getMediumIssues() { return mediumIssues; }
        public void setMediumIssues(int mediumIssues) { this.mediumIssues = mediumIssues; }

        public int getLowIssues() { return lowIssues; }
        public void setLowIssues(int lowIssues) { this.lowIssues = lowIssues; }

        public String getLastAnalysisDate() { return lastAnalysisDate; }
        public void setLastAnalysisDate(String lastAnalysisDate) { this.lastAnalysisDate = lastAnalysisDate; }
    }

    public static class DetailedStatsResponse {
        private int totalIssues;
        private int criticalIssues;
        private int highIssues;
        private int mediumIssues;
        private int lowIssues;
        //TODO: add resolved issues count to the stats
        private String lastAnalysisDate;
        private Map<String, Integer> issuesByType;
        private List<RecentAnalysis> recentAnalyses;
        private List<TopFile> topFiles;
        private Map<String, BranchStat> branchStats;
        private String trend;

        public int getTotalIssues() { return totalIssues; }
        public void setTotalIssues(int totalIssues) { this.totalIssues = totalIssues; }

        public int getCriticalIssues() { return criticalIssues; }
        public void setCriticalIssues(int criticalIssues) { this.criticalIssues = criticalIssues; }

        public int getHighIssues() { return highIssues; }
        public void setHighIssues(int highIssues) { this.highIssues = highIssues; }

        public int getMediumIssues() { return mediumIssues; }
        public void setMediumIssues(int mediumIssues) { this.mediumIssues = mediumIssues; }

        public int getLowIssues() { return lowIssues; }
        public void setLowIssues(int lowIssues) { this.lowIssues = lowIssues; }

        public String getLastAnalysisDate() { return lastAnalysisDate; }
        public void setLastAnalysisDate(String lastAnalysisDate) { this.lastAnalysisDate = lastAnalysisDate; }

        public Map<String, Integer> getIssuesByType() { return issuesByType; }
        public void setIssuesByType(Map<String, Integer> issuesByType) { this.issuesByType = issuesByType; }

        public List<RecentAnalysis> getRecentAnalyses() { return recentAnalyses; }
        public void setRecentAnalyses(List<RecentAnalysis> recentAnalyses) { this.recentAnalyses = recentAnalyses; }

        public List<TopFile> getTopFiles() { return topFiles; }
        public void setTopFiles(List<TopFile> topFiles) { this.topFiles = topFiles; }

        public Map<String, BranchStat> getBranchStats() { return branchStats; }
        public void setBranchStats(Map<String, BranchStat> branchStats) { this.branchStats = branchStats; }

        public String getTrend() { return trend; }
        public void setTrend(String trend) { this.trend = trend; }

        public static class RecentAnalysis {
            private String date;
            private int totalIssues;
            private String targetBranch;
            private String sourceBranch;
            private String status;

            public String getDate() { return date; }
            public void setDate(String date) { this.date = date; }

            public int getTotalIssues() { return totalIssues; }
            public void setTotalIssues(int totalIssues) { this.totalIssues = totalIssues; }

            public String getTargetBranch() { return targetBranch; }
            public void setTargetBranch(String targetBranch) { this.targetBranch = targetBranch; }

            public String getSourceBranch() { return sourceBranch; }
            public void setSourceBranch(String sourceBranch) { this.sourceBranch = sourceBranch; }

            public String getStatus() { return status; }
            public void setStatus(String status) { this.status = status; }
        }

        public static class TopFile {
            private String file;
            private int issues;
            private String severity;

            public String getFile() { return file; }
            public void setFile(String file) { this.file = file; }

            public int getIssues() { return issues; }
            public void setIssues(int issues) { this.issues = issues; }

            public String getSeverity() { return severity; }
            public void setSeverity(String severity) { this.severity = severity; }
        }

        public static class BranchStat {
            private int totalIssues;
            private String lastScan;

            public int getTotalIssues() { return totalIssues; }
            public void setTotalIssues(int totalIssues) { this.totalIssues = totalIssues; }

            public String getLastScan() { return lastScan; }
            public void setLastScan(String lastScan) { this.lastScan = lastScan; }
        }
    }

    public static class IssueStatusUpdateRequest {
        private boolean isResolved;
        private String comment;

        public boolean isResolved() { return isResolved; }
        public void setIsResolved(boolean resolved) { this.isResolved = resolved; }

        public String getComment() { return comment; }
        public void setComment(String comment) { this.comment = comment; }
    }

    public static class BulkStatusUpdateRequest {
        private List<Long> issueIds;
        private boolean isResolved;
        private String comment;

        public List<Long> getIssueIds() { return issueIds; }
        public void setIssueIds(List<Long> issueIds) { this.issueIds = issueIds; }

        public boolean isResolved() { return isResolved; }
        public void setIsResolved(boolean resolved) { this.isResolved = resolved; }

        public String getComment() { return comment; }
        public void setComment(String comment) { this.comment = comment; }
    }

    public static class BulkStatusUpdateResponse {
        private int successCount;
        private int failureCount;
        private List<Long> failedIds;
        private String newStatus;

        public int getSuccessCount() { return successCount; }
        public void setSuccessCount(int successCount) { this.successCount = successCount; }

        public int getFailureCount() { return failureCount; }
        public void setFailureCount(int failureCount) { this.failureCount = failureCount; }

        public List<Long> getFailedIds() { return failedIds; }
        public void setFailedIds(List<Long> failedIds) { this.failedIds = failedIds; }

        public String getNewStatus() { return newStatus; }
        public void setNewStatus(String newStatus) { this.newStatus = newStatus; }
    }
}
