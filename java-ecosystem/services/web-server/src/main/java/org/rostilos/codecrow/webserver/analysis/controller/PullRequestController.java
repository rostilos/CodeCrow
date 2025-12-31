package org.rostilos.codecrow.webserver.analysis.controller;

import java.util.Collections;
import java.util.HashMap;
import java.util.List;
import java.util.Map;
import java.util.stream.Collectors;

import org.rostilos.codecrow.core.model.pullrequest.PullRequest;
import org.rostilos.codecrow.core.model.workspace.Workspace;
import org.rostilos.codecrow.core.persistence.repository.pullrequest.PullRequestRepository;
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

    public PullRequestController(PullRequestRepository pullRequestRepository, ProjectService projectService,
                                 WorkspaceService workspaceService, BranchRepository branchRepository,
                                 BranchIssueRepository branchIssueRepository) {
        this.pullRequestRepository = pullRequestRepository;
        this.projectService = projectService;
        this.workspaceService = workspaceService;
        this.branchRepository = branchRepository;
        this.branchIssueRepository = branchIssueRepository;
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
                .map(PullRequestDTO::fromPullRequest)
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
        Map<String, List<PullRequestDTO>> grouped = pullRequestList.stream()
                .collect(Collectors.groupingBy(
                        pr -> pr.getTargetBranchName() == null ? "unknown" : pr.getTargetBranchName(),
                        Collectors.mapping(PullRequestDTO::fromPullRequest, Collectors.toList())
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
        
        // Use paginated queries from repository
        org.springframework.data.domain.Pageable pageable = org.springframework.data.domain.PageRequest.of(page - 1, pageSize);
        org.springframework.data.domain.Page<BranchIssue> branchIssuePage;
        long total;
        
        if ("all".equalsIgnoreCase(status)) {
            branchIssuePage = branchIssueRepository.findAllByBranchIdPaged(branch.getId(), pageable);
            total = branchIssueRepository.countAllByBranchId(branch.getId());
        } else if ("resolved".equalsIgnoreCase(status)) {
            branchIssuePage = branchIssueRepository.findResolvedByBranchIdPaged(branch.getId(), pageable);
            total = branchIssueRepository.countResolvedByBranchId(branch.getId());
        } else {
            // Default to "open" (unresolved)
            branchIssuePage = branchIssueRepository.findUnresolvedByBranchIdPaged(branch.getId(), pageable);
            total = branchIssueRepository.countUnresolvedByBranchId(branch.getId());
        }
        
        List<IssueDTO> pagedIssues = branchIssuePage.getContent().stream()
                .map(bi -> IssueDTO.fromEntity(bi.getCodeAnalysisIssue()))
                .toList();

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
