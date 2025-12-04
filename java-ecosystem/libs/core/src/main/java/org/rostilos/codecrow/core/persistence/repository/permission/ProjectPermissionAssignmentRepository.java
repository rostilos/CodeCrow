package org.rostilos.codecrow.core.persistence.repository.permission;

import java.util.List;
import java.util.Optional;

import org.rostilos.codecrow.core.model.permission.ProjectPermissionAssignment;
import org.rostilos.codecrow.core.model.project.Project;
import org.rostilos.codecrow.core.model.user.User;
import org.springframework.data.jpa.repository.JpaRepository;

public interface ProjectPermissionAssignmentRepository extends JpaRepository<ProjectPermissionAssignment, Long> {
    List<ProjectPermissionAssignment> findByProject(Project project);
    Optional<ProjectPermissionAssignment> findByProjectAndUser(Project project, User user);
    void deleteByProject_Id(Long projectId);
}
