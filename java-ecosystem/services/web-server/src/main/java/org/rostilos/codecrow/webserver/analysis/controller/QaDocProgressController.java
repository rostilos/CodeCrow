package org.rostilos.codecrow.webserver.analysis.controller;

import org.rostilos.codecrow.core.model.project.Project;
import org.rostilos.codecrow.core.service.ProjectFeaturePolicy;
import org.rostilos.codecrow.security.annotations.IsWorkspaceMember;
import org.rostilos.codecrow.webserver.analysis.service.QaDocProgressService;
import org.rostilos.codecrow.webserver.project.service.ProjectService;
import org.rostilos.codecrow.webserver.workspace.service.WorkspaceService;
import org.springframework.web.bind.annotation.*;

@RestController
@IsWorkspaceMember
@RequestMapping("/api/{workspaceSlug}/project/{projectNamespace}/pull-requests/{prNumber}/qa-doc/progress")
public class QaDocProgressController {
    public record Update(String revision, String environment, Integer testCase, Boolean checked, String action) {}
    private final WorkspaceService workspaces;
    private final ProjectService projects;
    private final QaDocProgressService progress;
    private final ProjectFeaturePolicy features;

    public QaDocProgressController(WorkspaceService workspaces, ProjectService projects,
                                   QaDocProgressService progress, ProjectFeaturePolicy features) {
        this.workspaces = workspaces;
        this.projects = projects;
        this.progress = progress;
        this.features = features;
    }

    @GetMapping
    public QaDocProgressService.Progress get(@PathVariable String workspaceSlug, @PathVariable String projectNamespace,
                                             @PathVariable Long prNumber) {
        return progress.get(project(workspaceSlug, projectNamespace).getId(), prNumber);
    }

    @PatchMapping
    public QaDocProgressService.Progress update(@PathVariable String workspaceSlug, @PathVariable String projectNamespace,
                                                @PathVariable Long prNumber, @RequestBody Update request) {
        return progress.update(project(workspaceSlug, projectNamespace).getId(), prNumber, request.revision(),
                request.environment(), request.testCase(), request.checked(), request.action() == null ? "" : request.action());
    }

    private Project project(String workspaceSlug, String projectNamespace) {
        var workspace = workspaces.getWorkspaceBySlug(workspaceSlug);
        var project = projects.getProjectByWorkspaceAndNamespace(workspace.getId(), projectNamespace);
        features.requireQaDocumentation(project);
        return project;
    }
}
