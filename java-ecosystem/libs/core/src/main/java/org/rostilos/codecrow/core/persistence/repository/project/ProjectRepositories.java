package org.rostilos.codecrow.core.persistence.repository.project;

import java.util.List;
import java.util.Optional;
import java.util.UUID;

import org.rostilos.codecrow.core.model.project.Project;
import org.rostilos.codecrow.core.model.project.ProjectAiConnectionBinding;
import org.rostilos.codecrow.core.model.project.ProjectVcsConnectionBinding;
import org.rostilos.codecrow.core.model.project.ProjectMember;
import org.springframework.data.jpa.repository.JpaRepository;
import org.springframework.stereotype.Repository;

public final class ProjectRepositories {
    private ProjectRepositories() {}

    @Repository
    public interface ProjectRepository extends JpaRepository<Project, UUID> {
        List<Project> findByUserId(Long userId);
    }

    @Repository
    public interface ProjectMemberRepository extends JpaRepository<ProjectMember, UUID> {
        List<ProjectMember> findByProjectId(UUID projectId);
        List<ProjectMember> findByUserId(UUID userId);
        Optional<ProjectMember> findByProjectIdAndUserId(UUID projectId, UUID userId);
    }

    @Repository
    public interface ProjectCodeHostingBindingRepository extends JpaRepository<ProjectVcsConnectionBinding, UUID> {
        Optional<ProjectVcsConnectionBinding> findByRepositoryUuid(String repositoryUuid);
        List<ProjectVcsConnectionBinding> findByProjectId(UUID projectId);
    }

    @Repository
    public interface ProjectAiBindingRepository extends JpaRepository<ProjectAiConnectionBinding, UUID> {
        Optional<ProjectAiConnectionBinding> findByProjectId(UUID projectId);
    }
}
