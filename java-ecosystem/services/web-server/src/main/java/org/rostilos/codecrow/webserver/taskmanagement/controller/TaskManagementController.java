package org.rostilos.codecrow.webserver.taskmanagement.controller;

import jakarta.validation.Valid;
import org.rostilos.codecrow.core.dto.taskmanagement.QaAutoDocConfigRequest;
import org.rostilos.codecrow.core.dto.taskmanagement.TaskManagementConnectionRequest;
import org.rostilos.codecrow.core.dto.taskmanagement.TaskManagementConnectionResponse;
import org.rostilos.codecrow.core.model.project.config.QaAutoDocConfig;
import org.rostilos.codecrow.core.model.workspace.Workspace;
import org.rostilos.codecrow.webserver.taskmanagement.service.TaskManagementService;
import org.rostilos.codecrow.webserver.workspace.service.WorkspaceService;
import org.slf4j.Logger;
import org.slf4j.LoggerFactory;
import org.springframework.http.HttpStatus;
import org.springframework.http.ResponseEntity;
import org.springframework.security.access.prepost.PreAuthorize;
import org.springframework.web.bind.annotation.*;

import java.util.List;
import java.util.Map;

/**
 * REST controller for workspace-level task management connections
 * and project-level QA auto-documentation configuration.
 */
@RestController
@RequestMapping("/api/{workspaceSlug}/task-management")
@PreAuthorize("isAuthenticated()")
public class TaskManagementController {

    private static final Logger log = LoggerFactory.getLogger(TaskManagementController.class);

    private final TaskManagementService taskManagementService;
    private final WorkspaceService workspaceService;

    public TaskManagementController(TaskManagementService taskManagementService,
                                     WorkspaceService workspaceService) {
        this.taskManagementService = taskManagementService;
        this.workspaceService = workspaceService;
    }

    // ─── Connection CRUD ─────────────────────────────────────────────

    @GetMapping("/connections")
    public ResponseEntity<List<TaskManagementConnectionResponse>> listConnections(
            @PathVariable String workspaceSlug) {
        Workspace workspace = resolveWorkspace(workspaceSlug);
        return ResponseEntity.ok(taskManagementService.listConnections(workspace.getId()));
    }

    @GetMapping("/connections/{connectionId}")
    public ResponseEntity<TaskManagementConnectionResponse> getConnection(
            @PathVariable String workspaceSlug,
            @PathVariable Long connectionId) {
        Workspace workspace = resolveWorkspace(workspaceSlug);
        return ResponseEntity.ok(taskManagementService.getConnection(workspace.getId(), connectionId));
    }

    @PostMapping("/connections")
    public ResponseEntity<TaskManagementConnectionResponse> createConnection(
            @PathVariable String workspaceSlug,
            @Valid @RequestBody TaskManagementConnectionRequest request) {
        Workspace workspace = resolveWorkspace(workspaceSlug);
        TaskManagementConnectionResponse response =
                taskManagementService.createConnection(workspace.getId(), workspace, request);
        return ResponseEntity.status(HttpStatus.CREATED).body(response);
    }

    @PutMapping("/connections/{connectionId}")
    public ResponseEntity<TaskManagementConnectionResponse> updateConnection(
            @PathVariable String workspaceSlug,
            @PathVariable Long connectionId,
            @Valid @RequestBody TaskManagementConnectionRequest request) {
        Workspace workspace = resolveWorkspace(workspaceSlug);
        return ResponseEntity.ok(
                taskManagementService.updateConnection(workspace.getId(), connectionId, request));
    }

    @DeleteMapping("/connections/{connectionId}")
    public ResponseEntity<Void> deleteConnection(
            @PathVariable String workspaceSlug,
            @PathVariable Long connectionId) {
        Workspace workspace = resolveWorkspace(workspaceSlug);
        taskManagementService.deleteConnection(workspace.getId(), connectionId);
        return ResponseEntity.noContent().build();
    }

    // ─── Connection validation ───────────────────────────────────────

    @PostMapping("/connections/{connectionId}/validate")
    public ResponseEntity<TaskManagementConnectionResponse> validateConnection(
            @PathVariable String workspaceSlug,
            @PathVariable Long connectionId) {
        Workspace workspace = resolveWorkspace(workspaceSlug);
        return ResponseEntity.ok(
                taskManagementService.validateConnection(workspace.getId(), connectionId));
    }

    // ─── QA Auto-Doc Config (project-level) ──────────────────────────

    @PutMapping("/projects/{projectId}/qa-auto-doc")
    public ResponseEntity<QaAutoDocConfig> updateQaAutoDocConfig(
            @PathVariable String workspaceSlug,
            @PathVariable Long projectId,
            @Valid @RequestBody QaAutoDocConfigRequest request) {
        // Workspace resolution for auth context
        resolveWorkspace(workspaceSlug);
        QaAutoDocConfig config = taskManagementService.updateQaAutoDocConfig(projectId, request);
        return ResponseEntity.ok(config);
    }

    // ─── Supported providers ─────────────────────────────────────────

    @GetMapping("/providers")
    public ResponseEntity<List<Map<String, Object>>> listProviders() {
        List<Map<String, Object>> providers = List.of(
                Map.of("id", "jira-cloud",
                       "name", "Jira Cloud",
                       "supported", true,
                       "description", "Atlassian Jira Cloud (atlassian.net)"),
                Map.of("id", "jira-data-center",
                       "name", "Jira Data Center",
                       "supported", false,
                       "description", "Jira Data Center / Server (self-hosted) — Coming Soon")
        );
        return ResponseEntity.ok(providers);
    }

    // ─── Error handling ──────────────────────────────────────────────

    @ExceptionHandler(IllegalArgumentException.class)
    public ResponseEntity<Map<String, String>> handleBadRequest(IllegalArgumentException e) {
        return ResponseEntity.badRequest().body(Map.of("error", e.getMessage()));
    }

    @ExceptionHandler(UnsupportedOperationException.class)
    public ResponseEntity<Map<String, String>> handleUnsupported(UnsupportedOperationException e) {
        return ResponseEntity.status(HttpStatus.NOT_IMPLEMENTED).body(Map.of("error", e.getMessage()));
    }

    // ─── Helpers ─────────────────────────────────────────────────────

    private Workspace resolveWorkspace(String workspaceSlug) {
        return workspaceService.getWorkspaceBySlug(workspaceSlug);
    }
}
