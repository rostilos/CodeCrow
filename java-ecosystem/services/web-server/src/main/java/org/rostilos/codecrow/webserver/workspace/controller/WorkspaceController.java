package org.rostilos.codecrow.webserver.workspace.controller;

import java.util.List;
import java.util.NoSuchElementException;
import jakarta.validation.Valid;
import org.rostilos.codecrow.core.dto.workspace.WorkspaceDTO;
import org.rostilos.codecrow.core.dto.workspace.WorkspaceMemberDTO;
import org.rostilos.codecrow.core.model.workspace.EWorkspaceRole;
import org.rostilos.codecrow.core.model.workspace.Workspace;
import org.rostilos.codecrow.core.model.workspace.WorkspaceMember;
import org.rostilos.codecrow.security.annotations.HasOwnerOrAdminRights;
import org.rostilos.codecrow.security.annotations.IsWorkspaceMember;
import org.rostilos.codecrow.security.annotations.IsWorkspaceOwner;
import org.rostilos.codecrow.security.service.UserDetailsImpl;
import org.rostilos.codecrow.webserver.generic.dto.message.MessageResponse;
import org.rostilos.codecrow.webserver.workspace.dto.request.ChangeRoleRequest;
import org.rostilos.codecrow.webserver.workspace.dto.request.CreateRequest;
import org.rostilos.codecrow.webserver.workspace.dto.request.DeleteWorkspaceRequest;
import org.rostilos.codecrow.webserver.workspace.dto.request.InviteRequest;
import org.rostilos.codecrow.webserver.workspace.dto.request.RemoveMemberRequest;
import org.rostilos.codecrow.webserver.workspace.service.WorkspaceService;
import org.rostilos.codecrow.webserver.auth.service.TwoFactorAuthService;
import org.springframework.http.HttpStatus;
import org.springframework.http.ResponseEntity;
import org.springframework.security.access.prepost.PreAuthorize;
import org.springframework.security.core.annotation.AuthenticationPrincipal;
import org.springframework.web.bind.annotation.*;

//TODO: invite + accept via email

@CrossOrigin(origins = "*", maxAge = 3600)
@RestController
@RequestMapping("/api/workspace")
public class WorkspaceController {

    private final WorkspaceService workspaceService;
    private final TwoFactorAuthService twoFactorAuthService;

    public WorkspaceController(WorkspaceService workspaceService, TwoFactorAuthService twoFactorAuthService) {
        this.workspaceService = workspaceService;
        this.twoFactorAuthService = twoFactorAuthService;
    }

    @GetMapping("list")
    @PreAuthorize("isAuthenticated()")
    public ResponseEntity<List<WorkspaceDTO>> listUserWorkspaces(@AuthenticationPrincipal UserDetailsImpl userDetails) {
        List<Workspace> userWorkspaces = workspaceService.listUserWorkspaces(userDetails.getId());
        List<WorkspaceDTO> workspaceDTOs = userWorkspaces.stream()
                .map(WorkspaceDTO::fromWorkspace)
                .toList();

        return new ResponseEntity<>(workspaceDTOs, HttpStatus.OK);
    }

    @PostMapping("/create")
    @PreAuthorize("isAuthenticated()")
    public ResponseEntity<WorkspaceDTO> createWorkspace(
            @AuthenticationPrincipal UserDetailsImpl userDetails,
            @Valid @RequestBody CreateRequest request
    ) {
        Workspace w = workspaceService.createWorkspace(userDetails.getId(), request.slug(), request.name(), request.description());
        return ResponseEntity.status(HttpStatus.CREATED).body(WorkspaceDTO.fromWorkspace(w));
    }

    @PostMapping("/{workspaceSlug}/invite")
    @HasOwnerOrAdminRights
    public ResponseEntity<MessageResponse> inviteToWorkspace(
            @AuthenticationPrincipal UserDetailsImpl userDetails,
            @PathVariable String workspaceSlug,
            @Valid @RequestBody InviteRequest request
    ) {
        workspaceService.inviteToWorkspace(userDetails.getId(), workspaceSlug, request.username(),
                request.role() == null ? null : EWorkspaceRole.valueOf(request.role().toUpperCase()));
        return ResponseEntity.ok(new MessageResponse("User successfully added to workspace!"));

    }

    @DeleteMapping("/{workspaceSlug}/member/remove")
    @HasOwnerOrAdminRights
    public ResponseEntity<MessageResponse> removeMember(
            @AuthenticationPrincipal UserDetailsImpl userDetails,
            @PathVariable String workspaceSlug,
            @Valid @RequestBody RemoveMemberRequest request
    ) {
        workspaceService.removeMemberFromWorkspace(userDetails.getId(), workspaceSlug, request.username());
        return ResponseEntity.ok(new MessageResponse(String.format("User  %s successfully removed from workspace!", request.username())));

    }

    @PutMapping("/{workspaceSlug}/changeRole")
    @HasOwnerOrAdminRights
    public ResponseEntity<MessageResponse> changeWorkspaceRole(
            @AuthenticationPrincipal UserDetailsImpl userDetails,
            @PathVariable String workspaceSlug,
            @RequestBody ChangeRoleRequest request
    ) {
        workspaceService.changeWorkspaceRole(
                userDetails.getId(),
                workspaceSlug,
                request.username(),
                EWorkspaceRole.valueOf(request.newRole().toUpperCase())
        );
        return ResponseEntity.ok(new MessageResponse("Role successfully changed!"));
    }

    @PostMapping("/{workspaceSlug}/invite/accept")
    @PreAuthorize("isAuthenticated()")
    public ResponseEntity<WorkspaceMemberDTO> acceptInvite(
            @AuthenticationPrincipal UserDetailsImpl userDetails,
            @PathVariable String workspaceSlug
    ) {
        WorkspaceMember wm = workspaceService.acceptInvite(userDetails.getId(), workspaceSlug);
        return ResponseEntity.ok(WorkspaceMemberDTO.fromEntity(wm));
    }

    @GetMapping("/{workspaceSlug}/members")
    @HasOwnerOrAdminRights
    public ResponseEntity<List<WorkspaceMemberDTO>> listMembers(
            @AuthenticationPrincipal UserDetailsImpl userDetails,
            @PathVariable String workspaceSlug
    ) {
        List<WorkspaceMember> list = workspaceService.listMembers(workspaceSlug);
        return ResponseEntity.ok(list.stream()
                .map(WorkspaceMemberDTO::fromEntity)
                .toList());
    }

    @GetMapping("/{workspaceSlug}/role")
    @IsWorkspaceMember
    public ResponseEntity<RoleResponse> getUserRole(
            @AuthenticationPrincipal UserDetailsImpl userDetails,
            @PathVariable String workspaceSlug
    ) {
        EWorkspaceRole role = workspaceService.getUserRole(workspaceSlug, userDetails.getId());
        if (role == null) {
            throw new NoSuchElementException("User is not an active member of the workspace");
        }
        return ResponseEntity.ok(new RoleResponse(role.name()));
    }

    @DeleteMapping("/{workspaceSlug}")
    @IsWorkspaceOwner
    public ResponseEntity<MessageResponse> deleteWorkspace(
            @AuthenticationPrincipal UserDetailsImpl userDetails,
            @PathVariable String workspaceSlug,
            @Valid @RequestBody DeleteWorkspaceRequest request
    ) {
        if (!workspaceSlug.equals(request.confirmationSlug())) {
            throw new IllegalArgumentException("Confirmation slug does not match workspace slug");
        }
        
        if (!twoFactorAuthService.verifyLoginCode(userDetails.getId(), request.twoFactorCode())) {
            throw new SecurityException("Invalid 2FA code");
        }
        
        Workspace workspace = workspaceService.scheduleDeletion(userDetails.getId(), workspaceSlug);
        return ResponseEntity.ok(new MessageResponse("Workspace scheduled for deletion on " + workspace.getScheduledDeletionAt()));
    }

    @PostMapping("/{workspaceSlug}/cancel-deletion")
    @IsWorkspaceOwner
    public ResponseEntity<MessageResponse> cancelScheduledDeletion(
            @AuthenticationPrincipal UserDetailsImpl userDetails,
            @PathVariable String workspaceSlug
    ) {
        workspaceService.cancelScheduledDeletion(userDetails.getId(), workspaceSlug);
        return ResponseEntity.ok(new MessageResponse("Workspace deletion cancelled"));
    }

    @GetMapping("/{workspaceSlug}/deletion-status")
    @IsWorkspaceMember
    public ResponseEntity<DeletionStatusResponse> getDeletionStatus(
            @AuthenticationPrincipal UserDetailsImpl userDetails,
            @PathVariable String workspaceSlug
    ) {
        Workspace workspace = workspaceService.getWorkspaceBySlug(workspaceSlug);
        return ResponseEntity.ok(new DeletionStatusResponse(
                workspace.isScheduledForDeletion(),
                workspace.getScheduledDeletionAt() != null ? workspace.getScheduledDeletionAt().toString() : null,
                workspace.getDeletionRequestedAt() != null ? workspace.getDeletionRequestedAt().toString() : null,
                workspace.getDeletionRequestedBy()
        ));
    }

    public record RoleResponse(String role) {}
    
    public record DeletionStatusResponse(
            boolean isScheduledForDeletion,
            String scheduledDeletionAt,
            String deletionRequestedAt,
            Long deletionRequestedBy
    ) {}
}
