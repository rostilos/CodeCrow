package org.rostilos.codecrow.webserver.reviewcheck;

import org.rostilos.codecrow.security.annotations.IsWorkspaceMember;
import org.rostilos.codecrow.security.annotations.IsWorkspaceReviewer;
import org.rostilos.codecrow.webserver.project.service.ProjectService;
import org.rostilos.codecrow.webserver.workspace.service.WorkspaceService;
import org.springframework.http.HttpStatus;
import org.springframework.web.bind.annotation.*;

@RestController
@RequestMapping("/api/{workspaceSlug}/project/{projectNamespace}/analysis/review-checks")
public class ReviewCheckController {
    private final ReviewCheckManagementService checks;
    private final WorkspaceService workspaces;
    private final ProjectService projects;

    public ReviewCheckController(ReviewCheckManagementService checks, WorkspaceService workspaces, ProjectService projects) {
        this.checks = checks; this.workspaces = workspaces; this.projects = projects;
    }

    @GetMapping
    @IsWorkspaceMember
    public ReviewCheckResponse list(@PathVariable String workspaceSlug, @PathVariable String projectNamespace,
                                    @RequestParam Long pullRequestId, @RequestParam(defaultValue = "0") int prVersion) {
        return checks.get(projectId(workspaceSlug, projectNamespace), pullRequestId, prVersion);
    }

    public record StatusUpdate(boolean resolved, String reason) {}

    @PutMapping("/findings/{findingId}/status")
    @IsWorkspaceReviewer
    @ResponseStatus(HttpStatus.NO_CONTENT)
    public void status(@PathVariable String workspaceSlug, @PathVariable String projectNamespace,
                       @PathVariable Long findingId, @RequestBody StatusUpdate update) {
        checks.updateStatus(projectId(workspaceSlug, projectNamespace), findingId, update.resolved(), update.reason());
    }

    private Long projectId(String workspaceSlug, String namespace) {
        return projects.getProjectByWorkspaceAndNamespace(workspaces.getWorkspaceBySlug(workspaceSlug).getId(), namespace).getId();
    }
}
