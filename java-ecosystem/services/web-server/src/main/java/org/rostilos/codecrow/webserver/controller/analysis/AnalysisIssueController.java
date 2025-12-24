package org.rostilos.codecrow.webserver.controller.analysis;

import org.rostilos.codecrow.core.dto.analysis.issue.IssueDTO;
import org.rostilos.codecrow.core.model.codeanalysis.CodeAnalysisIssue;
import org.rostilos.codecrow.core.model.workspace.Workspace;
import org.rostilos.codecrow.core.service.CodeAnalysisService;
import org.rostilos.codecrow.webserver.dto.request.analysis.issue.IssueStatusUpdateRequest;
import org.rostilos.codecrow.webserver.service.project.AnalysisService;
import org.rostilos.codecrow.webserver.service.project.ProjectService;
import org.rostilos.codecrow.core.model.project.Project;
import org.rostilos.codecrow.core.dto.analysis.issue.IssuesSummaryDTO;
import org.rostilos.codecrow.webserver.dto.response.analysis.AnalysisIssueResponse;
import org.rostilos.codecrow.webserver.service.workspace.WorkspaceService;
import org.springframework.http.HttpStatus;
import org.springframework.http.ResponseEntity;
import org.springframework.security.access.prepost.PreAuthorize;
import org.springframework.web.bind.annotation.*;

import java.util.List;
import java.util.Optional;

@CrossOrigin(origins = "*", maxAge = 3600)
@RestController
@RequestMapping("/api/{workspaceSlug}/project/{projectNamespace}/analysis/issues")
public class AnalysisIssueController {

    private final AnalysisService analysisService;
    private final ProjectService projectService;
    private final CodeAnalysisService codeAnalysisService;
    private final WorkspaceService workspaceService;

    public AnalysisIssueController(
            AnalysisService analysisService,
            ProjectService projectService,
            CodeAnalysisService codeAnalysisService,
            WorkspaceService workspaceService
    ) {
        this.analysisService = analysisService;
        this.projectService = projectService;
        this.codeAnalysisService = codeAnalysisService;
        this.workspaceService = workspaceService;
    }

    //TODO: rework it, it should be retrived as full analysis, not the only issues
    /**
     * GET /{workspaceId}/api/project/{projectId}/analysis/issues
     * Query params: ?branch={branch}&pullRequestId={prId}&severity={critical|high|medium|low}&type={security|quality|performance|style}
     */
    @GetMapping
    @PreAuthorize("@workspaceSecurity.isWorkspaceMember(#workspaceSlug, authentication)")
    public ResponseEntity<AnalysisIssueResponse> listIssues(
            @PathVariable String workspaceSlug,
            @PathVariable String projectNamespace,
            @RequestParam(name = "branch", required = false) String branch,
            @RequestParam(name = "pullRequestId", required = false) String pullRequestId,
            @RequestParam(name = "severity", required = false) String severity,
            @RequestParam(name = "type", required = false) String type,
            @RequestParam(name = "page", defaultValue = "1") int page,
            @RequestParam(name = "pageSize", defaultValue = "50") int pageSize,
            @RequestParam(name = "prVersion", defaultValue = "0") int prVersion
    ) {
        AnalysisIssueResponse resp = new AnalysisIssueResponse();
        Workspace workspace = workspaceService.getWorkspaceBySlug(workspaceSlug);
        Project project = projectService.getProjectByWorkspaceAndNamespace(workspace.getId(), projectNamespace);

        int maxVersion = 0;
        if(pullRequestId != null) {
            maxVersion = codeAnalysisService.getMaxAnalysisPrVersion(project.getId(), Long.parseLong(pullRequestId));
            resp.setMaxVersion(maxVersion);
        }
        List<CodeAnalysisIssue> issues = analysisService.findIssues(project.getId(), branch, pullRequestId, severity, type, (prVersion > 0 ? prVersion : maxVersion));
        List<IssueDTO> issueDTOs = issues.stream()
                .map(IssueDTO::fromEntity)
                .toList();

        IssuesSummaryDTO summary = IssuesSummaryDTO.fromIssuesDTOs(issueDTOs);


        resp.setIssues(issueDTOs);
        resp.setSummary(summary);

        return ResponseEntity.ok(resp);
    }

    @GetMapping("/{issueId}")
    @PreAuthorize("@workspaceSecurity.isWorkspaceMember(#workspaceSlug, authentication)")
    public ResponseEntity<IssueDTO> getIssueById(@PathVariable Long issueId, @PathVariable String workspaceSlug) {
        Optional<CodeAnalysisIssue> optionalIssue = analysisService.findIssueById(issueId);

        if(optionalIssue.isEmpty()) {
            return ResponseEntity.notFound().build();
        }else {
            CodeAnalysisIssue issue = optionalIssue.get();
            return new ResponseEntity<>(IssueDTO.fromEntity(issue), HttpStatus.OK);
        }
    }

    @PutMapping("/{issueId}/status")
    @PreAuthorize("@workspaceSecurity.hasOwnerOrAdminRights(#workspaceSlug, authentication)")
    public ResponseEntity<HttpStatus> updateIssueStatus(
            @PathVariable String workspaceSlug,
            @PathVariable Long issueId,
            @RequestBody IssueStatusUpdateRequest request
    ) {
        boolean ok = analysisService.updateIssueStatus(issueId, request.isResolved(), request.comment(), null);
        return ok ? new ResponseEntity<>(HttpStatus.NO_CONTENT) : new ResponseEntity<>(HttpStatus.NOT_FOUND);
    }
}
