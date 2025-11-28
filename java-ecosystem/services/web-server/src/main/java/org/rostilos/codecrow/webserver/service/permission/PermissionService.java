package org.rostilos.codecrow.webserver.service.permission;

import java.util.List;
import java.util.NoSuchElementException;
import java.util.Optional;
import java.util.Set;
import java.util.stream.Collectors;

import org.rostilos.codecrow.core.model.permission.PermissionTemplate;
import org.rostilos.codecrow.core.model.permission.PermissionType;
import org.rostilos.codecrow.core.model.permission.ProjectPermissionAssignment;
import org.rostilos.codecrow.core.model.workspace.EMembershipStatus;
import org.rostilos.codecrow.core.model.workspace.EWorkspaceRole;
import org.rostilos.codecrow.core.model.project.Project;
import org.rostilos.codecrow.core.model.user.User;
import org.rostilos.codecrow.core.model.user.account_type.EAccountType;
import org.rostilos.codecrow.core.persistence.repository.permission.PermissionTemplateRepository;
import org.rostilos.codecrow.core.persistence.repository.permission.ProjectPermissionAssignmentRepository;
import org.rostilos.codecrow.core.persistence.repository.project.ProjectRepository;
import org.rostilos.codecrow.core.persistence.repository.workspace.WorkspaceMemberRepository;
import org.rostilos.codecrow.core.persistence.repository.user.UserRepository;
import org.rostilos.codecrow.webserver.dto.permission.PermissionTemplateDTO;
import org.rostilos.codecrow.webserver.dto.permission.ProjectPermissionAssignmentDTO;
import org.springframework.stereotype.Service;
import org.springframework.transaction.annotation.Transactional;

import jakarta.persistence.EntityManager;
import jakarta.persistence.PersistenceContext;

@Service
public class PermissionService {

    @PersistenceContext
    private EntityManager entityManager;

    private final PermissionTemplateRepository permissionTemplateRepository;
    private final ProjectPermissionAssignmentRepository projectPermissionAssignmentRepository;
    private final ProjectRepository projectRepository;
    private final UserRepository userRepository;
    private final WorkspaceMemberRepository workspaceMemberRepository;

    public PermissionService(
            PermissionTemplateRepository permissionTemplateRepository,
            ProjectPermissionAssignmentRepository projectPermissionAssignmentRepository,
            ProjectRepository projectRepository,
            UserRepository userRepository,
            WorkspaceMemberRepository workspaceMemberRepository
    ) {
        this.permissionTemplateRepository = permissionTemplateRepository;
        this.projectPermissionAssignmentRepository = projectPermissionAssignmentRepository;
        this.projectRepository = projectRepository;
        this.userRepository = userRepository;
        this.workspaceMemberRepository = workspaceMemberRepository;
    }

    private boolean isAdmin(Long userId) {
        User u = entityManager.getReference(User.class, userId);
        return u.getAccountType() == EAccountType.TYPE_ADMIN;
    }

    private boolean isProjectOwner(Project project, Long userId) {
        if (project.getWorkspace() == null || project.getWorkspace().getId() == null) return false;
        return workspaceMemberRepository.findByWorkspaceIdAndUserId(project.getWorkspace().getId(), userId)
                .map(m -> m.getRole() == EWorkspaceRole.OWNER && m.getStatus() == EMembershipStatus.ACTIVE)
                .orElse(false);
    }

    @Transactional
    public PermissionTemplateDTO createTemplate(Long actorUserId, String name, String description, Set<PermissionType> permissions) {
        if (!isAdmin(actorUserId)) {
            throw new SecurityException("Only admins can create permission templates");
        }
        PermissionTemplate t = new PermissionTemplate();
        t.setName(name);
        t.setDescription(description);
        t.setPermissions(permissions);
        User creator = entityManager.getReference(User.class, actorUserId);
        t.setCreatedBy(creator);
        PermissionTemplate saved = permissionTemplateRepository.save(t);
        return mapTemplateToDto(saved);
    }

    @Transactional(readOnly = true)
    public List<PermissionTemplateDTO> listTemplates() {
        return permissionTemplateRepository.findAll().stream()
                .map(this::mapTemplateToDto)
                .collect(Collectors.toList());
    }

    @Transactional
    public ProjectPermissionAssignmentDTO assignTemplateToUser(Long actorUserId, Long projectId, Long targetUserId, Long templateId) {
        Project project = projectRepository.findById(projectId)
                .orElseThrow(() -> new NoSuchElementException("Project not found"));
        if (!(isAdmin(actorUserId) || isProjectOwner(project, actorUserId))) {
            throw new SecurityException("Only admins or project owner can assign templates");
        }

        User targetUser = userRepository.findById(targetUserId)
                .orElseThrow(() -> new NoSuchElementException("User not found"));

        PermissionTemplate template = permissionTemplateRepository.findById(templateId)
                .orElseThrow(() -> new NoSuchElementException("Template not found"));

        // Prevent duplicates using unique constraint; do lookup first
        Optional<ProjectPermissionAssignment> existing = projectPermissionAssignmentRepository.findByProjectAndUser(project, targetUser);
        ProjectPermissionAssignment pa;
        if (existing.isPresent()) {
            pa = existing.get();
            pa.setTemplate(template);
            pa = projectPermissionAssignmentRepository.save(pa);
        } else {
            pa = new ProjectPermissionAssignment();
            pa.setProject(project);
            pa.setUser(targetUser);
            pa.setTemplate(template);
            pa = projectPermissionAssignmentRepository.save(pa);
        }
        return mapAssignmentToDto(pa);
    }

    @Transactional(readOnly = true)
    public List<ProjectPermissionAssignmentDTO> listAssignmentsForProject(Long projectId) {
        Project project = projectRepository.findById(projectId)
                .orElseThrow(() -> new NoSuchElementException("Project not found"));
        return projectPermissionAssignmentRepository.findByProject(project).stream()
                .map(this::mapAssignmentToDto)
                .collect(Collectors.toList());
    }

    @Transactional
    public void removeAssignment(Long actorUserId, Long projectId, Long targetUserId) {
        Project project = projectRepository.findById(projectId)
                .orElseThrow(() -> new NoSuchElementException("Project not found"));
        if (!(isAdmin(actorUserId) || isProjectOwner(project, actorUserId))) {
            throw new SecurityException("Only admins or project owner can remove assignments");
        }

        User targetUser = userRepository.findById(targetUserId)
                .orElseThrow(() -> new NoSuchElementException("User not found"));

        Optional<ProjectPermissionAssignment> existing = projectPermissionAssignmentRepository.findByProjectAndUser(project, targetUser);
        existing.ifPresent(projectPermissionAssignmentRepository::delete);
    }

    @Transactional(readOnly = true)
    public Set<PermissionType> getEffectivePermissions(Long projectId, Long userId) {
        Project project = projectRepository.findById(projectId)
                .orElseThrow(() -> new NoSuchElementException("Project not found"));

        // owner has all permissions
        if (isProjectOwner(project, userId)) {
            return Set.of(PermissionType.values());
        }
        // admin has all permissions
        if (isAdmin(userId)) {
            return Set.of(PermissionType.values());
        }
        User user = entityManager.getReference(User.class, userId);
        Optional<ProjectPermissionAssignment> pa = projectPermissionAssignmentRepository.findByProjectAndUser(project, user);
        return pa.map(a -> a.getTemplate().getPermissions()).orElse(Set.of());
    }

    private PermissionTemplateDTO mapTemplateToDto(PermissionTemplate t) {
        if (t == null) return null;
        Long createdById = t.getCreatedBy() == null ? null : t.getCreatedBy().getId();
        return new PermissionTemplateDTO(
                t.getId(),
                t.getName(),
                t.getDescription(),
                t.getPermissions() == null ? Set.of() : t.getPermissions().stream().map(Enum::name).collect(Collectors.toSet()),
                createdById,
                t.getCreatedAt()
        );
    }

    private ProjectPermissionAssignmentDTO mapAssignmentToDto(ProjectPermissionAssignment pa) {
        if (pa == null) return null;
        PermissionTemplateDTO tpl = mapTemplateToDto(pa.getTemplate());
        String projectName = pa.getProject() == null ? null : pa.getProject().getName();
        Long userId = pa.getUser() == null ? null : pa.getUser().getId();
        return new ProjectPermissionAssignmentDTO(pa.getId(), projectName, userId, tpl);
    }
}
