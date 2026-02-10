package org.rostilos.codecrow.webserver.project.controller;

import jakarta.validation.Valid;
import jakarta.validation.constraints.NotBlank;
import org.rostilos.codecrow.core.model.project.AllowedCommandUser;
import org.rostilos.codecrow.core.model.project.Project;
import org.rostilos.codecrow.core.model.workspace.Workspace;
import org.rostilos.codecrow.security.annotations.HasOwnerOrAdminRights;
import org.rostilos.codecrow.webserver.project.service.AllowedCommandUserService;
import org.rostilos.codecrow.webserver.project.service.ProjectService;
import org.rostilos.codecrow.webserver.workspace.service.WorkspaceService;
import org.slf4j.Logger;
import org.slf4j.LoggerFactory;
import org.springframework.http.HttpStatus;
import org.springframework.http.ResponseEntity;
import org.springframework.web.bind.annotation.*;

import java.time.OffsetDateTime;
import java.util.List;
import java.util.stream.Collectors;

/**
 * REST controller for managing allowed command users.
 * 
 * Allows workspace admins to:
 * - View the list of users allowed to execute CodeCrow commands
 * - Add/remove users from the allowed list
 * - Sync users from VCS collaborators
 * - Enable/disable individual users
 */
@CrossOrigin(origins = "*", maxAge = 3600)
@RestController
@HasOwnerOrAdminRights
@RequestMapping("/api/{workspaceSlug}/project/{projectNamespace}/allowed-users")
public class AllowedCommandUserController {
    
    private static final Logger log = LoggerFactory.getLogger(AllowedCommandUserController.class);
    
    private final AllowedCommandUserService allowedUserService;
    private final ProjectService projectService;
    private final WorkspaceService workspaceService;
    
    public AllowedCommandUserController(
            AllowedCommandUserService allowedUserService,
            ProjectService projectService,
            WorkspaceService workspaceService
    ) {
        this.allowedUserService = allowedUserService;
        this.projectService = projectService;
        this.workspaceService = workspaceService;
    }
    
    /**
     * GET /api/{workspaceSlug}/project/{projectNamespace}/allowed-users
     * Get all allowed command users for a project.
     */
    @GetMapping
    public ResponseEntity<AllowedUsersResponse> getAllowedUsers(
            @PathVariable String workspaceSlug,
            @PathVariable String projectNamespace,
            @RequestParam(required = false, defaultValue = "false") boolean enabledOnly
    ) {
        Project project = getProject(workspaceSlug, projectNamespace);
        
        List<AllowedCommandUser> users = enabledOnly 
            ? allowedUserService.getEnabledAllowedUsers(project.getId())
            : allowedUserService.getAllowedUsers(project.getId());
        
        List<AllowedUserDTO> userDTOs = users.stream()
            .map(AllowedUserDTO::fromEntity)
            .collect(Collectors.toList());
        
        long totalCount = allowedUserService.countAllowedUsers(project.getId());
        long enabledCount = allowedUserService.countEnabledAllowedUsers(project.getId());
        
        return ResponseEntity.ok(new AllowedUsersResponse(userDTOs, totalCount, enabledCount));
    }
    
    /**
     * POST /api/{workspaceSlug}/project/{projectNamespace}/allowed-users
     * Add a user to the allowed list.
     */
    @PostMapping
    public ResponseEntity<AllowedUserDTO> addAllowedUser(
            @PathVariable String workspaceSlug,
            @PathVariable String projectNamespace,
            @Valid @RequestBody AddAllowedUserRequest request
    ) {
        Project project = getProject(workspaceSlug, projectNamespace);
        
        AllowedCommandUser user = allowedUserService.addAllowedUser(
            project,
            request.vcsUserId(),
            request.vcsUsername(),
            request.displayName(),
            request.avatarUrl(),
            null, // repoPermission - will be fetched during sync
            false, // syncedFromVcs
            "manual" // addedBy
        );
        
        log.info("Added allowed user {} to project {}", request.vcsUsername(), project.getId());
        
        return ResponseEntity.status(HttpStatus.CREATED).body(AllowedUserDTO.fromEntity(user));
    }
    
    /**
     * DELETE /api/{workspaceSlug}/project/{projectNamespace}/allowed-users/{vcsUserId}
     * Remove a user from the allowed list.
     */
    @DeleteMapping("/{vcsUserId}")
    public ResponseEntity<Void> removeAllowedUser(
            @PathVariable String workspaceSlug,
            @PathVariable String projectNamespace,
            @PathVariable String vcsUserId
    ) {
        Project project = getProject(workspaceSlug, projectNamespace);
        
        allowedUserService.removeAllowedUser(project.getId(), vcsUserId);
        
        log.info("Removed allowed user {} from project {}", vcsUserId, project.getId());
        
        return ResponseEntity.noContent().build();
    }
    
    /**
     * PATCH /api/{workspaceSlug}/project/{projectNamespace}/allowed-users/{vcsUserId}/enabled
     * Enable or disable a user.
     */
    @PatchMapping("/{vcsUserId}/enabled")
    public ResponseEntity<AllowedUserDTO> setUserEnabled(
            @PathVariable String workspaceSlug,
            @PathVariable String projectNamespace,
            @PathVariable String vcsUserId,
            @RequestBody SetEnabledRequest request
    ) {
        Project project = getProject(workspaceSlug, projectNamespace);
        
        AllowedCommandUser user = allowedUserService.setUserEnabled(
            project.getId(), 
            vcsUserId, 
            request.enabled()
        );
        
        log.info("Set user {} enabled={} for project {}", vcsUserId, request.enabled(), project.getId());
        
        return ResponseEntity.ok(AllowedUserDTO.fromEntity(user));
    }
    
    /**
     * POST /api/{workspaceSlug}/project/{projectNamespace}/allowed-users/sync
     * Sync allowed users from VCS collaborators.
     * Fetches repository collaborators with write access and adds them to the allowed list.
     */
    @PostMapping("/sync")
    public ResponseEntity<SyncResultResponse> syncFromVcs(
            @PathVariable String workspaceSlug,
            @PathVariable String projectNamespace
    ) {
        Project project = getProject(workspaceSlug, projectNamespace);
        
        AllowedCommandUserService.SyncResult result = allowedUserService.syncFromVcs(project, "api");
        
        log.info("Synced allowed users for project {}: success={}, added={}, updated={}", 
            project.getId(), result.success(), result.added(), result.updated());
        
        return ResponseEntity.ok(new SyncResultResponse(
            result.success(),
            result.added(),
            result.updated(),
            result.totalFetched(),
            result.error()
        ));
    }
    
    /**
     * DELETE /api/{workspaceSlug}/project/{projectNamespace}/allowed-users
     * Clear all allowed users for a project.
     */
    @DeleteMapping
    public ResponseEntity<Void> clearAllowedUsers(
            @PathVariable String workspaceSlug,
            @PathVariable String projectNamespace
    ) {
        Project project = getProject(workspaceSlug, projectNamespace);
        
        allowedUserService.clearAllowedUsers(project.getId());
        
        log.info("Cleared all allowed users for project {}", project.getId());
        
        return ResponseEntity.noContent().build();
    }
    
    private Project getProject(String workspaceSlug, String projectNamespace) {
        Workspace workspace = workspaceService.getWorkspaceBySlug(workspaceSlug);
        return projectService.getProjectByWorkspaceAndNamespace(workspace.getId(), projectNamespace);
    }
    
    // ==================== DTOs ====================
    
    public record AllowedUsersResponse(
        List<AllowedUserDTO> users,
        long totalCount,
        long enabledCount
    ) {}
    
    public record AllowedUserDTO(
        String id,
        String vcsProvider,
        String vcsUserId,
        String vcsUsername,
        String displayName,
        String avatarUrl,
        String repoPermission,
        boolean syncedFromVcs,
        boolean enabled,
        String addedBy,
        OffsetDateTime createdAt,
        OffsetDateTime lastSyncedAt
    ) {
        public static AllowedUserDTO fromEntity(AllowedCommandUser entity) {
            return new AllowedUserDTO(
                entity.getId() != null ? entity.getId().toString() : null,
                entity.getVcsProvider() != null ? entity.getVcsProvider().name() : null,
                entity.getVcsUserId(),
                entity.getVcsUsername(),
                entity.getDisplayName(),
                entity.getAvatarUrl(),
                entity.getRepoPermission(),
                entity.isSyncedFromVcs(),
                entity.isEnabled(),
                entity.getAddedBy(),
                entity.getCreatedAt(),
                entity.getLastSyncedAt()
            );
        }
    }
    
    public record AddAllowedUserRequest(
        @NotBlank String vcsUserId,
        @NotBlank String vcsUsername,
        String displayName,
        String avatarUrl
    ) {}
    
    public record SetEnabledRequest(boolean enabled) {}
    
    public record SyncResultResponse(
        boolean success,
        int added,
        int updated,
        int totalFetched,
        String error
    ) {}
}
