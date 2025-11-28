package org.rostilos.codecrow.webserver.controller.permission;

import java.util.List;
import java.util.Set;
import java.util.stream.Collectors;

import org.rostilos.codecrow.core.model.permission.PermissionType;
import org.rostilos.codecrow.security.service.UserDetailsImpl;
import org.rostilos.codecrow.webserver.dto.permission.PermissionTemplateDTO;
import org.rostilos.codecrow.webserver.dto.permission.ProjectPermissionAssignmentDTO;
import org.rostilos.codecrow.webserver.service.permission.PermissionService;
import org.springframework.http.HttpStatus;
import org.springframework.http.ResponseEntity;
import org.springframework.security.access.prepost.PreAuthorize;
import org.springframework.security.core.annotation.AuthenticationPrincipal;
import org.springframework.web.bind.annotation.DeleteMapping;
import org.springframework.web.bind.annotation.GetMapping;
import org.springframework.web.bind.annotation.PathVariable;
import org.springframework.web.bind.annotation.PostMapping;
import org.springframework.web.bind.annotation.RequestBody;
import org.springframework.web.bind.annotation.RequestMapping;
import org.springframework.web.bind.annotation.RestController;

import jakarta.validation.constraints.NotBlank;

@RestController
@RequestMapping("/api/{workspaceId}/permission")
public class PermissionController {

    private final PermissionService permissionService;
    private final org.rostilos.codecrow.webserver.service.project.ProjectService projectService;

    public PermissionController(PermissionService permissionService, org.rostilos.codecrow.webserver.service.project.ProjectService projectService) {
        this.permissionService = permissionService;
        this.projectService = projectService;
    }

    // DTOs as simple static classes to keep files minimal
    public static class CreateTemplateRequest {
        @NotBlank
        public String name;
        public String description;
        public Set<String> permissions;
    }

    public static class AssignTemplateRequest {
        public Long targetUserId;
        public Long templateId;
    }

    @PostMapping("/template")
    @PreAuthorize("@workspaceSecurity.hasOwnerOrAdminRights(#workspaceId, authentication)")
    public ResponseEntity<?> createTemplate(@AuthenticationPrincipal UserDetailsImpl userDetails,
                                            @RequestBody CreateTemplateRequest request) {
        Set<PermissionType> perms = request.permissions == null ? Set.of() :
                request.permissions.stream()
                        .map(String::toUpperCase)
                        .map(PermissionType::valueOf)
                        .collect(Collectors.toSet());

        PermissionTemplateDTO created = permissionService.createTemplate(
                userDetails.getId(),
                request.name,
                request.description,
                perms
        );
        return new ResponseEntity<>(created, HttpStatus.CREATED);
    }

    @GetMapping("/template")
    @PreAuthorize("@workspaceSecurity.hasOwnerOrAdminRights(#workspaceId, authentication)")
    public ResponseEntity<List<PermissionTemplateDTO>> listTemplates() {
        return ResponseEntity.ok(permissionService.listTemplates());
    }

    @PostMapping("/project/{projectNamespace}/assign")
    @PreAuthorize("@workspaceSecurity.hasOwnerOrAdminRights(#workspaceId, authentication)")
    public ResponseEntity<?> assignTemplate(@PathVariable Long workspaceId,
                                            @AuthenticationPrincipal UserDetailsImpl userDetails,
                                            @PathVariable String projectNamespace,
                                            @RequestBody AssignTemplateRequest request) {
        org.rostilos.codecrow.core.model.project.Project project = projectService.getProjectByWorkspaceAndNamespace(workspaceId, projectNamespace);
        ProjectPermissionAssignmentDTO pa = permissionService.assignTemplateToUser(
                userDetails.getId(),
                project.getId(),
                request.targetUserId,
                request.templateId
        );
        return new ResponseEntity<>(pa, HttpStatus.CREATED);

    }

    @GetMapping("/project/{projectNamespace}/assignments")
    @PreAuthorize("@workspaceSecurity.hasOwnerOrAdminRights(#workspaceId, authentication)")
    public ResponseEntity<?> listAssignments(@PathVariable Long workspaceId, @PathVariable String projectNamespace) {
        org.rostilos.codecrow.core.model.project.Project project = projectService.getProjectByWorkspaceAndNamespace(workspaceId, projectNamespace);
        List<ProjectPermissionAssignmentDTO> list = permissionService.listAssignmentsForProject(project.getId());
        return ResponseEntity.ok(list);
    }

    @DeleteMapping("/project/{projectNamespace}/assignments/{targetUserId}")
    @PreAuthorize("@workspaceSecurity.hasOwnerOrAdminRights(#workspaceId, authentication)")
    public ResponseEntity<?> removeAssignment(
            @PathVariable Long workspaceId,
            @AuthenticationPrincipal UserDetailsImpl userDetails,
            @PathVariable String projectNamespace,
            @PathVariable Long targetUserId
    ) {
        org.rostilos.codecrow.core.model.project.Project project = projectService.getProjectByWorkspaceAndNamespace(workspaceId, projectNamespace);
        permissionService.removeAssignment(userDetails.getId(), project.getId(), targetUserId);
        return ResponseEntity.noContent().build();
    }
}
