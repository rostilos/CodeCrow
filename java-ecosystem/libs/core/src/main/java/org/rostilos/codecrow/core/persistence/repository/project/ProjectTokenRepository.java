package org.rostilos.codecrow.core.persistence.repository.project;

import java.time.Instant;
import java.util.List;
import java.util.Optional;

import org.rostilos.codecrow.core.model.project.ProjectToken;
import org.springframework.data.jpa.repository.JpaRepository;
import org.springframework.data.jpa.repository.Query;
import org.springframework.stereotype.Repository;

@Repository
public interface ProjectTokenRepository extends JpaRepository<ProjectToken, Long> {
    List<ProjectToken> findByProject_Id(Long projectId);

    Optional<ProjectToken> findByIdAndProject_Id(Long id, Long projectId);

    @Query("select t from ProjectToken t where t.project.id = :projectId and (t.expiresAt is null or t.expiresAt > :now)")
    List<ProjectToken> findActiveByProjectId(Long projectId, Instant now);
}
