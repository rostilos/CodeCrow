package org.rostilos.codecrow.webserver.workspace.service;

import java.util.List;

import org.rostilos.codecrow.core.model.workspace.EWorkspaceRole;
import org.rostilos.codecrow.core.model.workspace.Workspace;
import org.rostilos.codecrow.core.model.workspace.WorkspaceMember;

/**
 * Interface for workspace management operations.
 * <p>
 * This interface enables cloud implementations to extend or decorate
 * the base workspace service with additional capabilities like billing limits.
 */
public interface IWorkspaceService {

    // ==================== Core CRUD ====================

    Workspace createWorkspace(Long actorUserId, String slug, String name, String description);

    Workspace getWorkspaceById(Long id);

    Workspace getWorkspaceBySlug(String slug);

    void deleteWorkspace(Long actorUserId, String workspaceSlug);

    // ==================== Membership ====================

    WorkspaceMember inviteToWorkspace(Long actorUserId, Long workspaceId, String username, EWorkspaceRole role);

    WorkspaceMember inviteToWorkspace(Long actorUserId, String workspaceSlug, String username, EWorkspaceRole role);

    void removeMemberFromWorkspace(Long actorUserId, Long workspaceId, String username);

    void removeMemberFromWorkspace(Long actorUserId, String workspaceSlug, String username);

    void changeWorkspaceRole(Long actorUserId, Long workspaceId, String targetUsername, EWorkspaceRole newRole);

    void changeWorkspaceRole(Long actorUserId, String workspaceSlug, String targetUsername, EWorkspaceRole newRole);

    WorkspaceMember acceptInvite(Long userId, Long workspaceId);

    WorkspaceMember acceptInvite(Long userId, String workspaceSlug);

    // ==================== Queries ====================

    List<WorkspaceMember> listMembers(Long workspaceId);

    List<WorkspaceMember> listMembers(String workspaceSlug);

    List<WorkspaceMember> listMembers(Long workspaceId, List<String> excludeUsernames);

    List<Workspace> listUserWorkspaces(Long userId);

    EWorkspaceRole getUserRole(Long workspaceId, Long userId);

    EWorkspaceRole getUserRole(String workspaceSlug, Long userId);

    // ==================== Deletion Scheduling ====================

    Workspace scheduleDeletion(Long actorUserId, String workspaceSlug);

    Workspace cancelScheduledDeletion(Long actorUserId, String workspaceSlug);
}
