package org.rostilos.codecrow.security.web;

import org.rostilos.codecrow.core.model.workspace.EMembershipStatus;
import org.rostilos.codecrow.core.model.workspace.EWorkspaceRole;
import org.rostilos.codecrow.core.persistence.repository.workspace.WorkspaceMemberRepository;
import org.rostilos.codecrow.core.persistence.repository.workspace.WorkspaceRepository;
import org.rostilos.codecrow.security.service.UserDetailsImpl;
import org.springframework.security.core.Authentication;
import org.springframework.stereotype.Component;

@Component("workspaceSecurity")
public class WorkspaceSecurity {

    private final WorkspaceMemberRepository memberRepository;
    private final WorkspaceRepository workspaceRepository;

    public WorkspaceSecurity(WorkspaceMemberRepository memberRepository, WorkspaceRepository workspaceRepository) {
        this.memberRepository = memberRepository;
        this.workspaceRepository = workspaceRepository;
    }

    public boolean hasOwnerOrAdminRights(Long workspaceId, Authentication authentication) {
        UserDetailsImpl user = (UserDetailsImpl) authentication.getPrincipal();
        return memberRepository.findByWorkspaceIdAndUserId(workspaceId, user.getId())
                .map(m -> m.getRole() == EWorkspaceRole.OWNER || m.getRole() == EWorkspaceRole.ADMIN)
                .orElse(false);
    }

    public boolean hasOwnerOrAdminRights(String workspaceSlug, Authentication authentication) {
        return workspaceRepository.findBySlug(workspaceSlug)
                .map(w -> hasOwnerOrAdminRights(w.getId(), authentication))
                .orElse(false);
    }

    public boolean isWorkspaceMember(Long workspaceId, Authentication authentication) {
        UserDetailsImpl user = (UserDetailsImpl) authentication.getPrincipal();

        return memberRepository.findByWorkspaceIdAndUserId(workspaceId, user.getId())
                .map(m -> m.getStatus() == EMembershipStatus.ACTIVE)
                .orElse(false);
    }

    public boolean isWorkspaceMember(String workspaceSlug, Authentication authentication) {
        return workspaceRepository.findBySlug(workspaceSlug)
                .map(w -> isWorkspaceMember(w.getId(), authentication))
                .orElse(false);
    }

    public boolean hasRole(Long workspaceId, EWorkspaceRole role, Authentication authentication) {
        UserDetailsImpl user = (UserDetailsImpl) authentication.getPrincipal();
        return memberRepository.findByWorkspaceIdAndUserId(workspaceId, user.getId())
                .map(m -> m.getRole() == role)
                .orElse(false);
    }

    public boolean hasRole(String workspaceSlug, EWorkspaceRole role, Authentication authentication) {
        return workspaceRepository.findBySlug(workspaceSlug)
                .map(w -> hasRole(w.getId(), role, authentication))
                .orElse(false);
    }

    /**
     * Check if user is the workspace owner.
     * Use this for endpoints that require owner-only permissions (e.g., ownership transfer).
     */
    public boolean isWorkspaceOwner(Long workspaceId, Authentication authentication) {
        UserDetailsImpl user = (UserDetailsImpl) authentication.getPrincipal();
        return memberRepository.findByWorkspaceIdAndUserId(workspaceId, user.getId())
                .map(m -> m.getStatus() == EMembershipStatus.ACTIVE && m.getRole() == EWorkspaceRole.OWNER)
                .orElse(false);
    }

    public boolean isWorkspaceOwner(String workspaceSlug, Authentication authentication) {
        return workspaceRepository.findBySlug(workspaceSlug)
                .map(w -> isWorkspaceOwner(w.getId(), authentication))
                .orElse(false);
    }

    /**
     * Check if user has at least REVIEWER level access (REVIEWER, ADMIN, or OWNER).
     * Use this for endpoints that require code review participation permissions.
     */
    public boolean isWorkspaceReviewer(Long workspaceId, Authentication authentication) {
        UserDetailsImpl user = (UserDetailsImpl) authentication.getPrincipal();
        return memberRepository.findByWorkspaceIdAndUserId(workspaceId, user.getId())
                .map(m -> m.getStatus() == EMembershipStatus.ACTIVE &&
                        (m.getRole() == EWorkspaceRole.OWNER ||
                         m.getRole() == EWorkspaceRole.ADMIN ||
                         m.getRole() == EWorkspaceRole.REVIEWER))
                .orElse(false);
    }

    public boolean isWorkspaceReviewer(String workspaceSlug, Authentication authentication) {
        return workspaceRepository.findBySlug(workspaceSlug)
                .map(w -> isWorkspaceReviewer(w.getId(), authentication))
                .orElse(false);
    }
}
