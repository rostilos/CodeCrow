package org.rostilos.codecrow.webserver.analysis.controller;

import java.util.Collections;
import java.util.HashMap;
import java.util.List;
import java.util.Map;
import java.util.Optional;
import java.util.function.Function;
import java.util.stream.Collectors;

import org.rostilos.codecrow.core.model.pullrequest.PullRequest;
import org.rostilos.codecrow.core.model.workspace.Workspace;
import org.rostilos.codecrow.core.model.codeanalysis.CodeAnalysis;
import org.rostilos.codecrow.core.persistence.repository.pullrequest.PullRequestRepository;
import org.rostilos.codecrow.core.persistence.repository.codeanalysis.CodeAnalysisRepository;
import org.rostilos.codecrow.security.annotations.HasOwnerOrAdminRights;
import org.rostilos.codecrow.security.annotations.IsWorkspaceMember;
import org.rostilos.codecrow.webserver.analysis.dto.request.IssueStatusUpdateRequest;
import org.rostilos.codecrow.webserver.analysis.dto.response.IssueStatusUpdateResponse;
import org.rostilos.codecrow.webserver.project.service.ProjectService;
import org.rostilos.codecrow.webserver.workspace.service.WorkspaceService;
import org.rostilos.codecrow.core.model.project.Project;
import org.rostilos.codecrow.core.persistence.repository.branch.BranchRepository;
import org.rostilos.codecrow.core.persistence.repository.branch.BranchIssueRepository;
import org.rostilos.codecrow.core.model.branch.Branch;
import org.rostilos.codecrow.core.model.branch.BranchIssue;
import org.rostilos.codecrow.core.dto.analysis.issue.IssueDTO;
import org.slf4j.Logger;
import org.slf4j.LoggerFactory;
import org.springframework.data.domain.Page;
import org.springframework.data.domain.PageRequest;
import org.springframework.http.HttpStatus;
import org.springframework.http.ResponseEntity;
import org.springframework.web.bind.annotation.*;
import org.rostilos.codecrow.core.dto.pullrequest.PullRequestDTO;

@CrossOrigin(origins = "*", maxAge = 3600)
@RestController
@IsWorkspaceMember
@RequestMapping("/api/{workspaceSlug}/project/{projectNamespace}/pull-requests")
public class PullRequestController {

    private static final Logger log = LoggerFactory.getLogger(PullRequestController.class);

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
    public ResponseEntity<Map<String, Object>> listPullRequests(
            @PathVariable String workspaceSlug,
            @PathVariable String projectNamespace,
            @RequestParam(name = "page", defaultValue = "1") int page,
            @RequestParam(name = "pageSize", defaultValue = "20") int pageSize
    ) {
        Workspace workspace = workspaceService.getWorkspaceBySlug(workspaceSlug);
        Project project = projectService.getProjectByWorkspaceAndNamespace(workspace.getId(), projectNamespace);
        Long projectId = project.getId();

        // DB-level pagination (page is 1-based from client, Spring Data is 0-based)
        Page<PullRequest> prPage = pullRequestRepository.findByProject_IdOrderByPrNumberDesc(
                projectId, PageRequest.of(Math.max(0, page - 1), Math.min(pageSize, 200)));

        // Single batch query for latest analysis per PR (replaces N+1 individual queries)
        List<Long> prNumbers = prPage.getContent().stream()
                .map(PullRequest::getPrNumber)
                .toList();
        Map<Long, CodeAnalysis> analysisByPr = prNumbers.isEmpty()
                ? Map.of()
                : codeAnalysisRepository.findLatestAnalysisForPrNumbers(projectId, prNumbers).stream()
                        .collect(Collectors.toMap(CodeAnalysis::getPrNumber, Function.identity(), (a, b) -> a));

        List<PullRequestDTO> pullRequestDTOs = prPage.getContent().stream()
                .map(pr -> PullRequestDTO.fromPullRequestWithAnalysis(pr, analysisByPr.get(pr.getPrNumber())))
                .toList();

        Map<String, Object> response = new HashMap<>();
        response.put("content", pullRequestDTOs);
        response.put("totalElements", prPage.getTotalElements());
        response.put("totalPages", prPage.getTotalPages());
        response.put("currentPage", page);
        response.put("pageSize", pageSize);

        return new ResponseEntity<>(response, HttpStatus.OK);
    }

    @GetMapping("/by-branch")
    public ResponseEntity<Map<String, List<PullRequestDTO>>> listPullRequestsByBranch(
            @PathVariable String workspaceSlug,
            @PathVariable String projectNamespace
    ) {
        Workspace workspace = workspaceService.getWorkspaceBySlug(workspaceSlug);
        Project project = projectService.getProjectByWorkspaceAndNamespace(workspace.getId(), projectNamespace);
        Long projectId = project.getId();

        List<PullRequest> pullRequestList = pullRequestRepository.findByProject_IdOrderByPrNumberDesc(projectId);

        // Single batch query for latest analysis per PR (replaces N+1)
        Map<Long, CodeAnalysis> analysisByPr = codeAnalysisRepository.findLatestAnalysisPerPrNumber(projectId).stream()
                .collect(Collectors.toMap(CodeAnalysis::getPrNumber, Function.identity(), (a, b) -> a));

        // Convert PRs to DTOs with analysis results, maintaining order within each group
        Map<String, List<PullRequestDTO>> grouped = pullRequestList.stream()
                .collect(Collectors.groupingBy(
                        pr -> pr.getTargetBranchName() == null ? "unknown" : pr.getTargetBranchName(),
                        Collectors.mapping(pr -> PullRequestDTO.fromPullRequestWithAnalysis(
                                pr, analysisByPr.get(pr.getPrNumber())), Collectors.toList())
                ));

        // Include branches that have been analyzed but don't have PRs
        List<Branch> analyzedBranches = branchRepository.findByProjectId(projectId).stream()
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

    @GetMapping("/branches/issues")
    public ResponseEntity<Map<String, Object>> listBranchIssues(
            @PathVariable String workspaceSlug,
            @PathVariable String projectNamespace,
            @RequestParam String branchName,
            @RequestParam(required = false, defaultValue = "open") String status,
            @RequestParam(required = false) String severity,
            @RequestParam(required = false) String category,
            @RequestParam(required = false) String filePath,
            @RequestParam(required = false) String dateFrom,
            @RequestParam(required = false) String dateTo,
            @RequestParam(required = false) String author,
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
        String normalizedAuthor = (author == null || author.isBlank() || "ALL".equalsIgnoreCase(author)) ? null : author.toLowerCase();
        
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

        // Fetch all issues for the branch and filter in Java
        // BranchIssue is now a full entity — all data is on the entity itself, no JOIN needed
        List<BranchIssue> allBranchIssues = branchIssueRepository.findAllByBranchIdWithIssues(branch.getId());
        
        // Apply filters in Java using BranchIssue's own fields
        final java.time.OffsetDateTime finalDateFrom = parsedDateFrom;
        final java.time.OffsetDateTime finalDateTo = parsedDateTo;
        
        List<BranchIssue> filteredIssues = allBranchIssues.stream()
            .filter(bi -> {
                // Status filter
                if ("open".equals(normalizedStatus) && bi.isResolved()) return false;
                if ("resolved".equals(normalizedStatus) && !bi.isResolved()) return false;
                // "all" passes everything
                
                // Severity filter — uses BranchIssue's own field
                if (normalizedSeverity != null) {
                    if (bi.getSeverity() == null) return false;
                    if (!normalizedSeverity.equals(bi.getSeverity().name())) return false;
                }
                
                // Category filter — uses BranchIssue's own field
                if (normalizedCategory != null) {
                    if (bi.getIssueCategory() == null) return false;
                    if (!normalizedCategory.equals(bi.getIssueCategory().name())) return false;
                }
                
                // File path filter (partial match, case insensitive)
                if (normalizedFilePath != null) {
                    if (bi.getFilePath() == null) return false;
                    if (!bi.getFilePath().toLowerCase().contains(normalizedFilePath)) return false;
                }
                
                // Author filter (case insensitive match on username)
                if (normalizedAuthor != null) {
                    if (bi.getVcsAuthorUsername() == null) return false;
                    if (!bi.getVcsAuthorUsername().toLowerCase().contains(normalizedAuthor)) return false;
                }
                
                // Date from filter
                if (finalDateFrom != null && bi.getCreatedAt() != null) {
                    if (bi.getCreatedAt().isBefore(finalDateFrom)) return false;
                }
                
                // Date to filter
                if (finalDateTo != null && bi.getCreatedAt() != null) {
                    if (bi.getCreatedAt().isAfter(finalDateTo)) return false;
                }
                
                return true;
            })
            .sorted((a, b) -> Long.compare(b.getId(), a.getId()))
            .toList();
        
        long total = filteredIssues.size();
        
        // Apply pagination
        int startIndex = (page - 1) * pageSize;
        int endIndex = Math.min(startIndex + pageSize, filteredIssues.size());
        
        List<IssueDTO> pagedIssues = (startIndex < filteredIssues.size()) 
            ? filteredIssues.subList(startIndex, endIndex).stream()
                .map(IssueDTO::fromBranchIssue)
                .toList()
            : List.of();

        Map<String, Object> response = new HashMap<>();
        response.put("issues", pagedIssues);
        response.put("total", total);
        response.put("page", page);
        response.put("pageSize", pageSize);

        return ResponseEntity.ok(response);
    }

    // ────────────────────────────────────────────────────────────────────────
    //  Branch-issue–specific endpoints (operate on BranchIssue directly)
    // ────────────────────────────────────────────────────────────────────────

    /**
     * GET  /branches/issues/{issueId}
     * <p>
     * Fetch a single {@link BranchIssue} by its own primary key.
     * Returns the same {@link IssueDTO} shape as the PR-level endpoint so
     * the frontend can consume them identically.
     */
    @GetMapping("/branches/issues/{issueId}")
    public ResponseEntity<IssueDTO> getBranchIssueById(
            @PathVariable String workspaceSlug,
            @PathVariable String projectNamespace,
            @PathVariable Long issueId
    ) {
        Workspace workspace = workspaceService.getWorkspaceBySlug(workspaceSlug);
        Project project = projectService.getProjectByWorkspaceAndNamespace(workspace.getId(), projectNamespace);

        Optional<BranchIssue> opt = branchIssueRepository.findById(issueId);
        if (opt.isEmpty()) {
            return ResponseEntity.notFound().build();
        }

        BranchIssue bi = opt.get();

        // Validate that this issue belongs to a branch under the current project
        if (bi.getBranch() == null
                || bi.getBranch().getProject() == null
                || !bi.getBranch().getProject().getId().equals(project.getId())) {
            return ResponseEntity.notFound().build();
        }

        return ResponseEntity.ok(IssueDTO.fromBranchIssue(bi));
    }

    /**
     * PUT  /branches/issues/{issueId}/status
     * <p>
     * Resolve or re-open a single {@link BranchIssue}.
     * This is a <b>branch-local</b> operation — the origin
     * {@code CodeAnalysisIssue} is intentionally <b>NOT</b> mutated
     * so that PR-level historical data stays immutable.
     * <p>
     * After updating the issue, the parent {@link Branch} aggregate
     * counts are refreshed and persisted.
     */
    @PutMapping("/branches/issues/{issueId}/status")
    @HasOwnerOrAdminRights
    public ResponseEntity<IssueStatusUpdateResponse> updateBranchIssueStatus(
            @PathVariable String workspaceSlug,
            @PathVariable String projectNamespace,
            @PathVariable Long issueId,
            @RequestBody IssueStatusUpdateRequest request
    ) {
        Workspace workspace = workspaceService.getWorkspaceBySlug(workspaceSlug);
        Project project = projectService.getProjectByWorkspaceAndNamespace(workspace.getId(), projectNamespace);

        Optional<BranchIssue> opt = branchIssueRepository.findById(issueId);
        if (opt.isEmpty()) {
            log.warn("updateBranchIssueStatus: BranchIssue not found for id={}", issueId);
            return ResponseEntity.ok(IssueStatusUpdateResponse.failure(issueId, "Branch issue not found"));
        }

        BranchIssue bi = opt.get();

        // Validate ownership
        if (bi.getBranch() == null
                || bi.getBranch().getProject() == null
                || !bi.getBranch().getProject().getId().equals(project.getId())) {
            return ResponseEntity.ok(IssueStatusUpdateResponse.failure(issueId, "Branch issue not found"));
        }

        java.time.OffsetDateTime now = java.time.OffsetDateTime.now();
        bi.setResolved(request.isResolved());

        if (request.isResolved()) {
            bi.setResolvedAt(now);
            bi.setResolvedBy("manual");
            if (request.comment() != null && !request.comment().isBlank()) {
                bi.setResolvedDescription(request.comment());
            }
            if (request.resolvedByPr() != null) {
                bi.setResolvedInPrNumber(request.resolvedByPr());
            }
            if (request.resolvedCommitHash() != null && !request.resolvedCommitHash().isBlank()) {
                bi.setResolvedInCommitHash(request.resolvedCommitHash());
            }
        } else {
            // Re-opening — clear all resolution metadata
            bi.setResolvedAt(null);
            bi.setResolvedBy(null);
            bi.setResolvedDescription(null);
            bi.setResolvedInPrNumber(null);
            bi.setResolvedInCommitHash(null);
        }

        branchIssueRepository.save(bi);
        log.info("updateBranchIssueStatus: Saved BranchIssue id={}, isResolved={}", bi.getId(), bi.isResolved());

        // Refresh aggregate counts on the parent Branch entity
        Branch branch = bi.getBranch();
        branch.updateIssueCounts();
        branchRepository.save(branch);

        return ResponseEntity.ok(IssueStatusUpdateResponse.success(
                issueId,
                request.isResolved(),
                null,  // no analysis context for branch-local updates
                null,  // no analysis result
                branch.getTotalIssues(),
                branch.getHighSeverityCount(),
                branch.getMediumSeverityCount(),
                branch.getLowSeverityCount(),
                branch.getInfoSeverityCount(),
                branch.getResolvedCount()
        ));
    }

    /**
     * PUT  /branches/issues/bulk-status
     * <p>
     * Bulk resolve or re-open multiple {@link BranchIssue} records.
     * This is a <b>branch-local</b> operation — origin
     * {@code CodeAnalysisIssue} records are intentionally <b>NOT</b> mutated.
     */
    @PutMapping("/branches/issues/bulk-status")
    @HasOwnerOrAdminRights
    public ResponseEntity<Map<String, Object>> bulkUpdateBranchIssueStatus(
            @PathVariable String workspaceSlug,
            @PathVariable String projectNamespace,
            @RequestBody Map<String, Object> request
    ) {
        Workspace workspace = workspaceService.getWorkspaceBySlug(workspaceSlug);
        Project project = projectService.getProjectByWorkspaceAndNamespace(workspace.getId(), projectNamespace);

        @SuppressWarnings("unchecked")
        List<Number> rawIds = (List<Number>) request.get("issueIds");
        boolean isResolved = Boolean.TRUE.equals(request.get("isResolved"));
        String comment = (String) request.get("comment");

        int successCount = 0;
        int failureCount = 0;
        List<Long> failedIds = new java.util.ArrayList<>();
        java.util.Set<Long> branchIdsToRefresh = new java.util.HashSet<>();
        java.time.OffsetDateTime now = java.time.OffsetDateTime.now();

        for (Number rawId : rawIds) {
            Long issueId = rawId.longValue();
            Optional<BranchIssue> opt = branchIssueRepository.findById(issueId);
            if (opt.isEmpty()) {
                failureCount++;
                failedIds.add(issueId);
                continue;
            }

            BranchIssue bi = opt.get();
            // Validate ownership
            if (bi.getBranch() == null
                    || bi.getBranch().getProject() == null
                    || !bi.getBranch().getProject().getId().equals(project.getId())) {
                failureCount++;
                failedIds.add(issueId);
                continue;
            }

            bi.setResolved(isResolved);
            if (isResolved) {
                bi.setResolvedAt(now);
                bi.setResolvedBy("manual");
                if (comment != null && !comment.isBlank()) {
                    bi.setResolvedDescription(comment);
                }
            } else {
                bi.setResolvedAt(null);
                bi.setResolvedBy(null);
                bi.setResolvedDescription(null);
                bi.setResolvedInPrNumber(null);
                bi.setResolvedInCommitHash(null);
            }

            branchIssueRepository.save(bi);
            branchIdsToRefresh.add(bi.getBranch().getId());
            successCount++;
        }

        // Refresh aggregate counts on affected branches
        for (Long branchId : branchIdsToRefresh) {
            branchRepository.findByIdWithIssues(branchId).ifPresent(branch -> {
                branch.updateIssueCounts();
                branchRepository.save(branch);
            });
        }

        Map<String, Object> response = new HashMap<>();
        response.put("successCount", successCount);
        response.put("failureCount", failureCount);
        response.put("failedIds", failedIds);
        response.put("newStatus", isResolved ? "resolved" : "open");
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
