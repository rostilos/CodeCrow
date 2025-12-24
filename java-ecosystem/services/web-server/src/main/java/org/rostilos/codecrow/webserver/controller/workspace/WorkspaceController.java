package org.rostilos.codecrow.webserver.controller.workspace;

import java.util.List;
import java.util.NoSuchElementException;
import jakarta.validation.Valid;
import org.rostilos.codecrow.core.dto.workspace.WorkspaceDTO;
import org.rostilos.codecrow.core.dto.workspace.WorkspaceMemberDTO;
import org.rostilos.codecrow.core.model.workspace.EWorkspaceRole;
import org.rostilos.codecrow.core.model.workspace.Workspace;
import org.rostilos.codecrow.core.model.workspace.WorkspaceMember;
import org.rostilos.codecrow.security.service.UserDetailsImpl;
import org.rostilos.codecrow.webserver.dto.message.MessageResponse;
import org.rostilos.codecrow.webserver.dto.request.workspace.ChangeRoleRequest;
import org.rostilos.codecrow.webserver.dto.request.workspace.CreateRequest;
import org.rostilos.codecrow.webserver.dto.request.workspace.InviteRequest;
import org.rostilos.codecrow.webserver.dto.request.workspace.RemoveMemberRequest;
import org.rostilos.codecrow.webserver.service.workspace.WorkspaceService;
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

    public WorkspaceController(WorkspaceService workspaceService) {
        this.workspaceService = workspaceService;
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
    @PreAuthorize("@workspaceSecurity.hasOwnerOrAdminRights(#workspaceSlug, authentication)")
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
    @PreAuthorize("@workspaceSecurity.hasOwnerOrAdminRights(#workspaceSlug, authentication)")
    public ResponseEntity<MessageResponse> removeMember(
            @AuthenticationPrincipal UserDetailsImpl userDetails,
            @PathVariable String workspaceSlug,
            @Valid @RequestBody RemoveMemberRequest request
    ) {
        workspaceService.removeMemberFromWorkspace(userDetails.getId(), workspaceSlug, request.username());
        return ResponseEntity.ok(new MessageResponse(String.format("User  %s successfully removed from workspace!", request.username())));

    }

    @PutMapping("/{workspaceSlug}/changeRole")
    @PreAuthorize("@workspaceSecurity.hasOwnerOrAdminRights(#workspaceSlug, authentication)")
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
    @PreAuthorize("@workspaceSecurity.hasOwnerOrAdminRights(#workspaceSlug, authentication)")
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
    @PreAuthorize("@workspaceSecurity.isWorkspaceMember(#workspaceSlug, authentication)")
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

    public record RoleResponse(String role) {}
}
