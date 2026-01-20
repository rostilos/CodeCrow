package org.rostilos.codecrow.security.web;

import org.junit.jupiter.api.BeforeEach;
import org.junit.jupiter.api.Test;
import org.junit.jupiter.api.extension.ExtendWith;
import org.mockito.Mock;
import org.mockito.junit.jupiter.MockitoExtension;
import org.rostilos.codecrow.core.model.workspace.EMembershipStatus;
import org.rostilos.codecrow.core.model.workspace.EWorkspaceRole;
import org.rostilos.codecrow.core.model.workspace.Workspace;
import org.rostilos.codecrow.core.model.workspace.WorkspaceMember;
import org.rostilos.codecrow.core.persistence.repository.workspace.WorkspaceMemberRepository;
import org.rostilos.codecrow.core.persistence.repository.workspace.WorkspaceRepository;
import org.rostilos.codecrow.security.service.UserDetailsImpl;
import org.springframework.security.core.Authentication;

import java.util.Optional;

import static org.assertj.core.api.Assertions.assertThat;
import static org.mockito.Mockito.lenient;
import static org.mockito.Mockito.mock;
import static org.mockito.Mockito.when;

@ExtendWith(MockitoExtension.class)
class WorkspaceSecurityTest {

    @Mock
    private WorkspaceMemberRepository memberRepository;

    @Mock
    private WorkspaceRepository workspaceRepository;

    @Mock
    private Authentication authentication;

    private WorkspaceSecurity workspaceSecurity;
    private UserDetailsImpl userDetails;

    @BeforeEach
    void setUp() {
        workspaceSecurity = new WorkspaceSecurity(memberRepository, workspaceRepository);
        userDetails = mock(UserDetailsImpl.class);
        lenient().when(userDetails.getId()).thenReturn(1L);
        lenient().when(authentication.getPrincipal()).thenReturn(userDetails);
    }

    @Test
    void testHasOwnerOrAdminRights_WithOwnerId_ReturnsTrue() {
        WorkspaceMember member = new WorkspaceMember();
        member.setRole(EWorkspaceRole.OWNER);
        when(memberRepository.findByWorkspaceIdAndUserId(100L, 1L)).thenReturn(Optional.of(member));

        boolean result = workspaceSecurity.hasOwnerOrAdminRights(100L, authentication);

        assertThat(result).isTrue();
    }

    @Test
    void testHasOwnerOrAdminRights_WithAdminId_ReturnsTrue() {
        WorkspaceMember member = new WorkspaceMember();
        member.setRole(EWorkspaceRole.ADMIN);
        when(memberRepository.findByWorkspaceIdAndUserId(100L, 1L)).thenReturn(Optional.of(member));

        boolean result = workspaceSecurity.hasOwnerOrAdminRights(100L, authentication);

        assertThat(result).isTrue();
    }

    @Test
    void testHasOwnerOrAdminRights_WithMemberId_ReturnsFalse() {
        WorkspaceMember member = new WorkspaceMember();
        member.setRole(EWorkspaceRole.MEMBER);
        when(memberRepository.findByWorkspaceIdAndUserId(100L, 1L)).thenReturn(Optional.of(member));

        boolean result = workspaceSecurity.hasOwnerOrAdminRights(100L, authentication);

        assertThat(result).isFalse();
    }

    @Test
    void testHasOwnerOrAdminRights_NotMember_ReturnsFalse() {
        when(memberRepository.findByWorkspaceIdAndUserId(100L, 1L)).thenReturn(Optional.empty());

        boolean result = workspaceSecurity.hasOwnerOrAdminRights(100L, authentication);

        assertThat(result).isFalse();
    }

    @Test
    void testHasOwnerOrAdminRights_WithSlug_ReturnsTrue() {
        Workspace workspace = mock(Workspace.class);
        when(workspace.getId()).thenReturn(100L);
        WorkspaceMember member = new WorkspaceMember();
        member.setRole(EWorkspaceRole.OWNER);
        
        when(workspaceRepository.findBySlug("my-workspace")).thenReturn(Optional.of(workspace));
        when(memberRepository.findByWorkspaceIdAndUserId(100L, 1L)).thenReturn(Optional.of(member));

        boolean result = workspaceSecurity.hasOwnerOrAdminRights("my-workspace", authentication);

        assertThat(result).isTrue();
    }

    @Test
    void testHasOwnerOrAdminRights_WithSlug_WorkspaceNotFound_ReturnsFalse() {
        when(workspaceRepository.findBySlug("nonexistent")).thenReturn(Optional.empty());

        boolean result = workspaceSecurity.hasOwnerOrAdminRights("nonexistent", authentication);

        assertThat(result).isFalse();
    }

    @Test
    void testIsWorkspaceMember_ActiveMember_ReturnsTrue() {
        WorkspaceMember member = new WorkspaceMember();
        member.setStatus(EMembershipStatus.ACTIVE);
        when(memberRepository.findByWorkspaceIdAndUserId(100L, 1L)).thenReturn(Optional.of(member));

        boolean result = workspaceSecurity.isWorkspaceMember(100L, authentication);

        assertThat(result).isTrue();
    }

    @Test
    void testIsWorkspaceMember_PendingMember_ReturnsFalse() {
        WorkspaceMember member = new WorkspaceMember();
        member.setStatus(EMembershipStatus.PENDING);
        when(memberRepository.findByWorkspaceIdAndUserId(100L, 1L)).thenReturn(Optional.of(member));

        boolean result = workspaceSecurity.isWorkspaceMember(100L, authentication);

        assertThat(result).isFalse();
    }

    @Test
    void testIsWorkspaceMember_NotMember_ReturnsFalse() {
        when(memberRepository.findByWorkspaceIdAndUserId(100L, 1L)).thenReturn(Optional.empty());

        boolean result = workspaceSecurity.isWorkspaceMember(100L, authentication);

        assertThat(result).isFalse();
    }

    @Test
    void testIsWorkspaceMember_WithSlug_ReturnsTrue() {
        Workspace workspace = mock(Workspace.class);
        when(workspace.getId()).thenReturn(100L);
        WorkspaceMember member = new WorkspaceMember();
        member.setStatus(EMembershipStatus.ACTIVE);
        
        when(workspaceRepository.findBySlug("my-workspace")).thenReturn(Optional.of(workspace));
        when(memberRepository.findByWorkspaceIdAndUserId(100L, 1L)).thenReturn(Optional.of(member));

        boolean result = workspaceSecurity.isWorkspaceMember("my-workspace", authentication);

        assertThat(result).isTrue();
    }

    @Test
    void testHasRole_MatchingRole_ReturnsTrue() {
        WorkspaceMember member = new WorkspaceMember();
        member.setRole(EWorkspaceRole.ADMIN);
        when(memberRepository.findByWorkspaceIdAndUserId(100L, 1L)).thenReturn(Optional.of(member));

        boolean result = workspaceSecurity.hasRole(100L, EWorkspaceRole.ADMIN, authentication);

        assertThat(result).isTrue();
    }

    @Test
    void testHasRole_DifferentRole_ReturnsFalse() {
        WorkspaceMember member = new WorkspaceMember();
        member.setRole(EWorkspaceRole.MEMBER);
        when(memberRepository.findByWorkspaceIdAndUserId(100L, 1L)).thenReturn(Optional.of(member));

        boolean result = workspaceSecurity.hasRole(100L, EWorkspaceRole.ADMIN, authentication);

        assertThat(result).isFalse();
    }

    @Test
    void testHasRole_WithSlug_ReturnsTrue() {
        Workspace workspace = mock(Workspace.class);
        when(workspace.getId()).thenReturn(100L);
        WorkspaceMember member = new WorkspaceMember();
        member.setRole(EWorkspaceRole.VIEWER);
        
        when(workspaceRepository.findBySlug("my-workspace")).thenReturn(Optional.of(workspace));
        when(memberRepository.findByWorkspaceIdAndUserId(100L, 1L)).thenReturn(Optional.of(member));

        boolean result = workspaceSecurity.hasRole("my-workspace", EWorkspaceRole.VIEWER, authentication);

        assertThat(result).isTrue();
    }
}
