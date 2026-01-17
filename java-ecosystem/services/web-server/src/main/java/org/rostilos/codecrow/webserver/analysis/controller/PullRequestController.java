package org.rostilos.codecrow.webserver.analysis.controller;

import java.util.Collections;
import java.util.HashMap;
import java.util.List;
import java.util.Map;
import java.util.stream.Collectors;

import org.rostilos.codecrow.core.model.pullrequest.PullRequest;
import org.rostilos.codecrow.core.model.workspace.Workspace;
import org.rostilos.codecrow.core.model.codeanalysis.CodeAnalysis;
import org.rostilos.codecrow.core.persistence.repository.pullrequest.PullRequestRepository;
import org.rostilos.codecrow.core.persistence.repository.codeanalysis.CodeAnalysisRepository;
import org.rostilos.codecrow.webserver.project.service.ProjectService;
import org.rostilos.codecrow.webserver.workspace.service.WorkspaceService;
import org.rostilos.codecrow.core.model.project.Project;
import org.rostilos.codecrow.core.persistence.repository.branch.BranchRepository;
import org.rostilos.codecrow.core.persistence.repository.branch.BranchIssueRepository;
import org.rostilos.codecrow.core.model.branch.Branch;
import org.rostilos.codecrow.core.model.branch.BranchIssue;
import org.rostilos.codecrow.core.dto.analysis.issue.IssueDTO;
import org.springframework.http.HttpStatus;
import org.springframework.http.ResponseEntity;
import org.springframework.security.access.prepost.PreAuthorize;
import org.springframework.web.bind.annotation.*;
import org.rostilos.codecrow.core.dto.pullrequest.PullRequestDTO;

@CrossOrigin(origins = "*", maxAge = 3600)
@RestController
@RequestMapping("/api/{workspaceSlug}/project/{projectNamespace}/pull-requests")
public class PullRequestController {
    private final PullRequestRepository pullRequestRepository;
    private final ProjectService projectService;
    private final WorkspaceService workspaceService;
    private final BranchRepository branchRepository;
    private final BranchIssueRepository branchIssueRepository;
    private final CodeAnalysisRepository codeAnalysisRepository;

    public PullRequestController(PullRequestRepository pullRequestRepository, ProjectService projectService,
                                 WorkspaceService workspaceService, BranchRepository branchRepository,
                                 BranchIssueRepository branchIssueRepository,
                                 CodeAnalysisRepository codeAnalysisRepository) {
        this.pullRequestRepository = pullRequestRepository;
        this.projectService = projectService;
        this.workspaceService = workspaceService;
        this.branchRepository = branchRepository;
        this.branchIssueRepository = branchIssueRepository;
        this.codeAnalysisRepository = codeAnalysisRepository;
    }

    @GetMapping
    @PreAuthorize("@workspaceSecurity.isWorkspaceMember(#workspaceSlug, authentication)")
    public ResponseEntity<List<PullRequestDTO>> listPullRequests(
            @PathVariable String workspaceSlug,
            @PathVariable String projectNamespace,
            @RequestParam(name = "page", defaultValue = "1") int page,
            @RequestParam(name = "pageSize", defaultValue = "20") int pageSize
    ) {
        Workspace workspace = workspaceService.getWorkspaceBySlug(workspaceSlug);
        Project project = projectService.getProjectByWorkspaceAndNamespace(workspace.getId(), projectNamespace);
        List<PullRequest> pullRequestList = pullRequestRepository.findByProject_Id(project.getId());
        List<PullRequestDTO> pullRequestDTOs = pullRequestList.stream()
                .map(pr -> {
                    CodeAnalysis analysis = codeAnalysisRepository
                            .findByProjectIdAndPrNumberWithMaxPrVersion(project.getId(), pr.getPrNumber())
                            .orElse(null);
                    return PullRequestDTO.fromPullRequestWithAnalysis(pr, analysis);
                })
                .toList();

        return new ResponseEntity<>(pullRequestDTOs, HttpStatus.OK);
    }

    @GetMapping("/by-branch")
    @PreAuthorize("@workspaceSecurity.isWorkspaceMember(#workspaceSlug, authentication)")
    public ResponseEntity<Map<String, List<PullRequestDTO>>> listPullRequestsByBranch(
            @PathVariable String workspaceSlug,
            @PathVariable String projectNamespace
    ) {
        Workspace workspace = workspaceService.getWorkspaceBySlug(workspaceSlug);
        Project project = projectService.getProjectByWorkspaceAndNamespace(workspace.getId(), projectNamespace);
        List<PullRequest> pullRequestList = pullRequestRepository.findByProject_Id(project.getId());
        
        // Convert PRs to DTOs with analysis results
        Map<String, List<PullRequestDTO>> grouped = pullRequestList.stream()
                .collect(Collectors.groupingBy(
                        pr -> pr.getTargetBranchName() == null ? "unknown" : pr.getTargetBranchName(),
                        Collectors.mapping(pr -> {
                            CodeAnalysis analysis = codeAnalysisRepository
                                    .findByProjectIdAndPrNumberWithMaxPrVersion(project.getId(), pr.getPrNumber())
                                    .orElse(null);
                            return PullRequestDTO.fromPullRequestWithAnalysis(pr, analysis);
                        }, Collectors.toList())
                ));

        // Include branches that have been analyzed but don't have PRs
        List<Branch> analyzedBranches = branchRepository.findByProjectId(project.getId()).stream()
                .filter(branch -> branch.getTotalIssues() > 0 || !branch.getIssues().isEmpty())
                .toList();

        for (Branch branch : analyzedBranches) {
            String branchName = branch.getBranchName();
            if (!grouped.containsKey(branchName)) {
                grouped.put(branchName, Collections.emptyList());
            }
        }


        return new ResponseEntity<>(grouped, HttpStatus.OK);
    }

    @GetMapping("/branches/{branchName}/issues")
    @PreAuthorize("@workspaceSecurity.isWorkspaceMember(#workspaceSlug, authentication)")
    public ResponseEntity<Map<String, Object>> listBranchIssues(
            @PathVariable String workspaceSlug,
            @PathVariable String projectNamespace,
            @PathVariable String branchName,
            @RequestParam(required = false, defaultValue = "open") String status,
            @RequestParam(required = false) String severity,
            @RequestParam(required = false) String category,
            @RequestParam(required = false) String filePath,
            @RequestParam(required = false) String dateFrom,
            @RequestParam(required = false) String dateTo,
            @RequestParam(required = false, defaultValue = "1") int page,
            @RequestParam(required = false, defaultValue = "50") int pageSize
    ) {
        Workspace workspace = workspaceService.getWorkspaceBySlug(workspaceSlug);
        Project project = projectService.getProjectByWorkspaceAndNamespace(workspace.getId(), projectNamespace);
        var branchOpt = branchRepository.findByProjectIdAndBranchName(project.getId(), branchName);
        if (branchOpt.isEmpty()) {
            return ResponseEntity.ok(Map.of(
                "issues", List.of(),
                "total", 0,
                "page", page,
                "pageSize", pageSize
            ));
        }
        Branch branch = branchOpt.get();
        
        // Normalize filter values
        String normalizedStatus = (status == null || status.isBlank()) ? "open" : status.toLowerCase();
        String normalizedSeverity = (severity == null || severity.isBlank() || "ALL".equalsIgnoreCase(severity)) ? null : severity.toUpperCase();
        String normalizedCategory = (category == null || category.isBlank() || "ALL".equalsIgnoreCase(category)) ? null : category.toUpperCase();
        String normalizedFilePath = (filePath == null || filePath.isBlank()) ? null : filePath.toLowerCase();
        
        // Parse date filters
        java.time.OffsetDateTime parsedDateFrom = null;
        java.time.OffsetDateTime parsedDateTo = null;
        if (dateFrom != null && !dateFrom.isBlank()) {
            try {
                parsedDateFrom = java.time.OffsetDateTime.parse(dateFrom);
            } catch (Exception e) {
                try {
                    parsedDateFrom = java.time.LocalDate.parse(dateFrom).atStartOfDay().atOffset(java.time.ZoneOffset.UTC);
                } catch (Exception ignored) {}
            }
        }
        if (dateTo != null && !dateTo.isBlank()) {
            try {
                parsedDateTo = java.time.OffsetDateTime.parse(dateTo);
            } catch (Exception e) {
                try {
                    parsedDateTo = java.time.LocalDate.parse(dateTo).atTime(23, 59, 59).atOffset(java.time.ZoneOffset.UTC);
                } catch (Exception ignored) {}
            }
        }

        //TODO: use SQL instead....
        // Fetch all issues for the branch and filter in Java (avoids complex SQL with nullable params)
        List<BranchIssue> allBranchIssues = branchIssueRepository.findAllByBranchIdWithIssues(branch.getId());
        
        // Apply filters in Java
        final java.time.OffsetDateTime finalDateFrom = parsedDateFrom;
        final java.time.OffsetDateTime finalDateTo = parsedDateTo;
        
        List<BranchIssue> filteredIssues = allBranchIssues.stream()
            .filter(bi -> {
                // Status filter
                if ("open".equals(normalizedStatus) && bi.isResolved()) return false;
                if ("resolved".equals(normalizedStatus) && !bi.isResolved()) return false;
                // "all" passes everything
                
                var issue = bi.getCodeAnalysisIssue();
                if (issue == null) return false;
                
                // Severity filter
                if (normalizedSeverity != null) {
                    if (issue.getSeverity() == null) return false;
                    if (!normalizedSeverity.equals(issue.getSeverity().name())) return false;
                }
                
                // Category filter
                if (normalizedCategory != null) {
                    if (issue.getIssueCategory() == null) return false;
                    if (!normalizedCategory.equals(issue.getIssueCategory().name())) return false;
                }
                
                // File path filter (partial match, case insensitive)
                if (normalizedFilePath != null) {
                    if (issue.getFilePath() == null) return false;
                    if (!issue.getFilePath().toLowerCase().contains(normalizedFilePath)) return false;
                }
                
                // Date from filter
                if (finalDateFrom != null && issue.getCreatedAt() != null) {
                    if (issue.getCreatedAt().isBefore(finalDateFrom)) return false;
                }
                
                // Date to filter
                if (finalDateTo != null && issue.getCreatedAt() != null) {
                    if (issue.getCreatedAt().isAfter(finalDateTo)) return false;
                }
                
                return true;
            })
            .sorted((a, b) -> Long.compare(b.getCodeAnalysisIssue().getId(), a.getCodeAnalysisIssue().getId()))
            .toList();
        
        long total = filteredIssues.size();
        
        // Apply pagination
        int startIndex = (page - 1) * pageSize;
        int endIndex = Math.min(startIndex + pageSize, filteredIssues.size());
        
        List<IssueDTO> pagedIssues = (startIndex < filteredIssues.size()) 
            ? filteredIssues.subList(startIndex, endIndex).stream()
                .map(bi -> IssueDTO.fromEntity(bi.getCodeAnalysisIssue()))
                .toList()
            : List.of();

        Map<String, Object> response = new HashMap<>();
        response.put("issues", pagedIssues);
        response.put("total", total);
        response.put("page", page);
        response.put("pageSize", pageSize);

        return ResponseEntity.ok(response);
    }

    public static class UpdatePullRequestStatusRequest {
        private String status; // approved|changes_requested|merged|closed
        private String comment;

        public UpdatePullRequestStatusRequest() {
        }

        public String getStatus() {
            return status;
        }

        public void setStatus(String status) {
            this.status = status;
        }

        public String getComment() {
            return comment;
        }

        public void setComment(String comment) {
            this.comment = comment;
        }
    }
}
