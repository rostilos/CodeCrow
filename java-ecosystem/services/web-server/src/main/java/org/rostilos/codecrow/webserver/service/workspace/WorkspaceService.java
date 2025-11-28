package org.rostilos.codecrow.webserver.service.workspace;

import java.util.List;
import java.util.NoSuchElementException;
import java.util.Optional;

import org.rostilos.codecrow.core.model.workspace.EMembershipStatus;
import org.rostilos.codecrow.core.model.workspace.EWorkspaceRole;
import org.rostilos.codecrow.core.model.workspace.Workspace;
import org.rostilos.codecrow.core.model.workspace.WorkspaceMember;
import org.rostilos.codecrow.core.model.user.User;
import org.rostilos.codecrow.core.persistence.repository.workspace.WorkspaceMemberRepository;
import org.rostilos.codecrow.core.persistence.repository.workspace.WorkspaceRepository;
import org.rostilos.codecrow.core.persistence.repository.user.UserRepository;
import org.springframework.stereotype.Service;
import org.springframework.transaction.annotation.Transactional;

import jakarta.persistence.EntityManager;
import jakarta.persistence.PersistenceContext;

@Service
public class WorkspaceService {

    @PersistenceContext
    private EntityManager entityManager;

    private final WorkspaceRepository workspaceRepository;
    private final WorkspaceMemberRepository workspaceMemberRepository;
    private final UserRepository userRepository;

    public WorkspaceService(WorkspaceRepository workspaceRepository,
                            WorkspaceMemberRepository workspaceMemberRepository,
                            UserRepository userRepository) {
        this.workspaceRepository = workspaceRepository;
        this.workspaceMemberRepository = workspaceMemberRepository;
        this.userRepository = userRepository;
    }

    @Transactional
    public Workspace createWorkspace(Long actorUserId, String slug, String name, String description) {
        if (workspaceRepository.existsBySlug(slug)) {
            throw new IllegalArgumentException("Workspace with slug '" + slug + "' already exists");
        }

        Workspace w = new Workspace();
        w.setSlug(slug);
        w.setName(name);
        w.setDescription(description);
        Workspace saved = workspaceRepository.save(w);

        // actor becomes OWNER
        User actor = entityManager.getReference(User.class, actorUserId);
        WorkspaceMember wm = new WorkspaceMember();
        wm.setWorkspace(saved);
        wm.setUser(actor);
        wm.setRole(EWorkspaceRole.OWNER);
        wm.setStatus(EMembershipStatus.ACTIVE);
        workspaceMemberRepository.save(wm);
        return saved;
    }

    @Transactional(readOnly = true)
    public Workspace getWorkspaceBySlug(String slug) {
        return workspaceRepository.findBySlug(slug)
                .orElseThrow(() -> new IllegalArgumentException("Workspace not found: " + slug));
    }

    @Transactional(readOnly = true)
    public Workspace getWorkspaceById(Long id) {
        return workspaceRepository.findById(id)
                .orElseThrow(() -> new IllegalArgumentException("Workspace not found"));
    }

    @Transactional
    public void removeMemberFromWorkspace(Long actorUserId, Long workspaceId, String username) {
        User target = userRepository.findByUsername(username)
                .orElseThrow(() -> new IllegalArgumentException("User not found"));

        WorkspaceMember targetMember = workspaceMemberRepository.findByWorkspaceIdAndUserId(workspaceId, target.getId())
                .orElseThrow(() -> new IllegalArgumentException("Workspace Member not found"));

        workspaceMemberRepository.delete(targetMember);
    }

    @Transactional
    public void removeMemberFromWorkspace(Long actorUserId, String workspaceSlug, String username) {
        Workspace workspace = getWorkspaceBySlug(workspaceSlug);
        removeMemberFromWorkspace(actorUserId, workspace.getId(), username);
    }

    @Transactional
    public WorkspaceMember inviteToWorkspace(Long actorUserId, Long workspaceId, String username, EWorkspaceRole role) {
        Workspace w = workspaceRepository.findById(workspaceId)
                .orElseThrow(() -> new IllegalArgumentException("Workspace not found"));
        // check actor is owner/admin of workspace or site admin is checked by caller
        User target = userRepository.findByUsername(username)
                .orElseThrow(() -> new IllegalArgumentException("User not found"));

        WorkspaceMember wm = new WorkspaceMember();
        wm.setWorkspace(w);
        wm.setUser(target);
        wm.setRole(role == null ? EWorkspaceRole.MEMBER : role);
        wm.setStatus(EMembershipStatus.ACTIVE);
        return workspaceMemberRepository.save(wm);
    }

    @Transactional
    public WorkspaceMember inviteToWorkspace(Long actorUserId, String workspaceSlug, String username, EWorkspaceRole role) {
        Workspace workspace = getWorkspaceBySlug(workspaceSlug);
        return inviteToWorkspace(actorUserId, workspace.getId(), username, role);
    }

    @Transactional
    public void changeWorkspaceRole(Long actorUserId, Long workspaceId, String targetUsername, EWorkspaceRole newRole) {
        User target = userRepository.findByUsername(targetUsername)
                .orElseThrow(() -> new IllegalArgumentException("User not found"));

        Optional<WorkspaceMember> optionalWorkspaceMemberActor = workspaceMemberRepository.findByWorkspaceIdAndUserId(workspaceId, actorUserId);
        if(optionalWorkspaceMemberActor.isEmpty()) {
            throw new SecurityException("The current user does not have editing privileges.");
        }
        WorkspaceMember workspaceMemberActor = optionalWorkspaceMemberActor.get();
        if(workspaceMemberActor.getRole() == EWorkspaceRole.ADMIN && ( newRole == EWorkspaceRole.ADMIN || newRole == EWorkspaceRole.OWNER )) {
            throw new SecurityException("The current user does not have editing privileges.");
        }

        WorkspaceMember workspaceMember = workspaceMemberRepository.findByWorkspaceIdAndUserId(workspaceId, target.getId())
                .orElseThrow(() -> new NoSuchElementException("The user is not present in the workspace."));

        workspaceMember.setRole(newRole);
        workspaceMemberRepository.save(workspaceMember);
    }

    @Transactional
    public void changeWorkspaceRole(Long actorUserId, String workspaceSlug, String targetUsername, EWorkspaceRole newRole) {
        Workspace workspace = getWorkspaceBySlug(workspaceSlug);
        changeWorkspaceRole(actorUserId, workspace.getId(), targetUsername, newRole);
    }

    @Transactional
    public WorkspaceMember acceptInvite(Long userId, Long workspaceId) {
        WorkspaceMember wm = workspaceMemberRepository.findByWorkspaceIdAndUserId(workspaceId, userId)
                .orElseThrow(() -> new IllegalArgumentException("Invite not found"));
        if (wm.getStatus() != EMembershipStatus.PENDING) {
            throw new IllegalArgumentException("No pending invite for this user");
        }
        wm.setStatus(EMembershipStatus.ACTIVE);
        return workspaceMemberRepository.save(wm);
    }

    @Transactional
    public WorkspaceMember acceptInvite(Long userId, String workspaceSlug) {
        Workspace workspace = getWorkspaceBySlug(workspaceSlug);
        return acceptInvite(userId, workspace.getId());
    }

    /**
     * Returns all members for a workspace (keeps existing behaviour).
     */
    @Transactional(readOnly = true)
    public List<WorkspaceMember> listMembers(Long workspaceId) {
        return listMembers(workspaceId, null);
    }

    /**
     * Returns workspace members by slug.
     */
    @Transactional(readOnly = true)
    public List<WorkspaceMember> listMembers(String workspaceSlug) {
        Workspace workspace = getWorkspaceBySlug(workspaceSlug);
        return listMembers(workspace.getId(), null);
    }

    /**
     * Returns workspace members and optionally excludes members whose usernames are in excludeUsernames.
     * excludeUsernames can be null or empty (no exclusions).
     */
    @Transactional(readOnly = true)
    public List<WorkspaceMember> listMembers(Long workspaceId, List<String> excludeUsernames) {
        Workspace w = workspaceRepository.findById(workspaceId)
                .orElseThrow(() -> new IllegalArgumentException("Workspace not found"));
        List<WorkspaceMember> members = workspaceMemberRepository.findByWorkspace_Id(w.getId());
        if (excludeUsernames == null || excludeUsernames.isEmpty()) {
            return members;
        }
        members.removeIf(m -> m.getUser() != null && excludeUsernames.contains(m.getUser().getUsername()));
        return members;
    }

    @Transactional(readOnly = true)
    public List<Workspace> listUserWorkspaces(Long userId) {
        return workspaceMemberRepository.findActiveWorkspacesByUserId(userId);
    }

    @Transactional(readOnly = true)
    public EWorkspaceRole getUserRole(Long workspaceId, Long userId) {
        return workspaceMemberRepository.findByWorkspaceIdAndUserId(workspaceId, userId)
                .filter(wm -> wm.getStatus() == EMembershipStatus.ACTIVE)
                .map(WorkspaceMember::getRole)
                .orElse(null);
    }

    @Transactional(readOnly = true)
    public EWorkspaceRole getUserRole(String workspaceSlug, Long userId) {
        Workspace workspace = getWorkspaceBySlug(workspaceSlug);
        return getUserRole(workspace.getId(), userId);
    }
}
