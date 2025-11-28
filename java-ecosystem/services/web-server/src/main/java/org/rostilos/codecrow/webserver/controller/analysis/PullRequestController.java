package org.rostilos.codecrow.webserver.controller.analysis;

import java.util.List;
import java.util.Map;
import java.util.stream.Collectors;

import org.rostilos.codecrow.core.model.pullrequest.PullRequest;
import org.rostilos.codecrow.core.model.workspace.Workspace;
import org.rostilos.codecrow.core.persistence.repository.pullrequest.PullRequestRepository;
import org.rostilos.codecrow.webserver.service.project.ProjectService;
import org.rostilos.codecrow.webserver.service.workspace.WorkspaceService;
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
    public ResponseEntity<?> listPullRequests(
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
                .collect(Collectors.toList());

        return new ResponseEntity<>(pullRequestDTOs, HttpStatus.OK);
    }

    @PutMapping("/{prId}/status")
    @PreAuthorize("@workspaceSecurity.hasOwnerOrAdminRights(#workspaceSlug, authentication)")
    public ResponseEntity<?> updatePullRequestStatus(
            @PathVariable String workspaceSlug,
            @PathVariable String projectNamespace,
            @PathVariable String prId,
            @RequestBody UpdatePullRequestStatusRequest request
    ) {
        Workspace workspace = workspaceService.getWorkspaceBySlug(workspaceSlug);
        Project project = projectService.getProjectByWorkspaceAndNamespace(workspace.getId(), projectNamespace);
        return new ResponseEntity<>(HttpStatus.NO_CONTENT);
    }

    @GetMapping("/by-branch")
    @PreAuthorize("@workspaceSecurity.isWorkspaceMember(#workspaceSlug, authentication)")
    public ResponseEntity<?> listPullRequestsByBranch(
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
        return new ResponseEntity<>(grouped, HttpStatus.OK);
    }

    @GetMapping("/branches/{branchName}/issues")
    @PreAuthorize("@workspaceSecurity.isWorkspaceMember(#workspaceSlug, authentication)")
    public ResponseEntity<?> listBranchIssues(
            @PathVariable String workspaceSlug,
            @PathVariable String projectNamespace,
            @PathVariable String branchName
    ) {
        Workspace workspace = workspaceService.getWorkspaceBySlug(workspaceSlug);
        Project project = projectService.getProjectByWorkspaceAndNamespace(workspace.getId(), projectNamespace);
        var branchOpt = branchRepository.findByProjectIdAndBranchName(project.getId(), branchName);
        if (branchOpt.isEmpty()) {
            return ResponseEntity.ok(List.of());
        }
        Branch branch = branchOpt.get();
        List<BranchIssue> branchIssues = branchIssueRepository.findByBranchId(branch.getId());
        List<IssueDTO> issues = branchIssues.stream()
                .map(bi -> IssueDTO.fromEntity(bi.getCodeAnalysisIssue()))
                .toList();
        return ResponseEntity.ok(issues);
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
