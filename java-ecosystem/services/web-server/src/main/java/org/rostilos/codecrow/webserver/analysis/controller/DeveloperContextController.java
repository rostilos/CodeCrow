package org.rostilos.codecrow.webserver.analysis.controller;

import org.rostilos.codecrow.security.annotations.IsWorkspaceMember;
import org.rostilos.codecrow.webserver.analysis.service.DeveloperContextService;
import org.rostilos.codecrow.webserver.project.service.ProjectService;
import org.rostilos.codecrow.webserver.workspace.service.WorkspaceService;
import org.springframework.web.bind.annotation.*;
import java.util.Map;

@RestController
@IsWorkspaceMember
@RequestMapping("/api/{workspaceSlug}/project/{projectNamespace}/developer-context")
public class DeveloperContextController {
    private final WorkspaceService workspaces;
    private final ProjectService projects;
    private final DeveloperContextService context;

    public DeveloperContextController(WorkspaceService workspaces, ProjectService projects, DeveloperContextService context) {
        this.workspaces = workspaces; this.projects = projects; this.context = context;
    }

    @GetMapping
    public Map<String, Object> get(@PathVariable String workspaceSlug, @PathVariable String projectNamespace,
                                   @RequestParam(required = false) String branch,
                                   @RequestParam(required = false) String commitHash,
                                   @RequestParam(required = false) Long prNumber) {
        var workspace = workspaces.getWorkspaceBySlug(workspaceSlug);
        var project = projects.getProjectByWorkspaceAndNamespace(workspace.getId(), projectNamespace);
        return context.get(project.getId(), branch, commitHash, prNumber);
    }
}
