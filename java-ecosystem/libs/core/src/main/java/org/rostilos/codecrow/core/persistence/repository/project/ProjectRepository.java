package org.rostilos.codecrow.core.persistence.repository.project;

import java.util.List;
import java.util.Optional;

import org.rostilos.codecrow.core.model.project.Project;
import org.springframework.data.jpa.repository.JpaRepository;
import org.springframework.data.jpa.repository.Query;
import org.springframework.data.repository.query.Param;
import org.springframework.stereotype.Repository;

@Repository
public interface ProjectRepository extends JpaRepository<Project, Long> {
    List<Project> findByWorkspaceId(Long workspaceId);
    java.util.List<Project> findByWorkspace_IdIn(java.util.List<Long> workspaceIds);
    Optional<Project> findByWorkspaceIdAndId(Long workspaceId, Long id);
    Optional<Project> findByWorkspaceIdAndName(Long workspaceId, String name);
    Optional<Project> findByWorkspaceIdAndNamespace(Long workspaceId, String namespace);

    @Query("SELECT p FROM Project p LEFT JOIN FETCH p.defaultBranch WHERE p.workspace.id = :workspaceId")
    List<Project> findByWorkspaceIdWithDefaultBranch(@Param("workspaceId") Long workspaceId);

    @Query("SELECT p FROM Project p " +
           "LEFT JOIN FETCH p.workspace " +
           "LEFT JOIN FETCH p.vcsBinding vcs " +
           "LEFT JOIN FETCH vcs.vcsConnection " +
           "LEFT JOIN FETCH p.aiBinding ai " +
           "LEFT JOIN FETCH ai.aiConnection " +
           "WHERE p.id = :projectId")
    Optional<Project> findByIdWithConnections(@Param("projectId") Long projectId);

    @Query("SELECT p FROM Project p " +
           "LEFT JOIN FETCH p.workspace " +
           "LEFT JOIN FETCH p.vcsBinding vcs " +
           "LEFT JOIN FETCH vcs.vcsConnection vc " +
           "LEFT JOIN FETCH p.aiBinding ai " +
           "LEFT JOIN FETCH ai.aiConnection " +
           "LEFT JOIN FETCH p.defaultBranch " +
           "LEFT JOIN FETCH p.vcsRepoBinding vrb " +
           "LEFT JOIN FETCH vrb.vcsConnection " +
           "WHERE p.id = :projectId")
    Optional<Project> findByIdWithFullDetails(@Param("projectId") Long projectId);
}
