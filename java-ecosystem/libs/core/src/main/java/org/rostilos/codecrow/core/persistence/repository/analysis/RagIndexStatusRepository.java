package org.rostilos.codecrow.core.persistence.repository.analysis;

import org.rostilos.codecrow.core.model.analysis.RagIndexStatus;
import org.rostilos.codecrow.core.model.analysis.RagIndexingStatus;
import org.springframework.data.jpa.repository.JpaRepository;
import org.springframework.data.jpa.repository.Query;
import org.springframework.data.repository.query.Param;
import org.springframework.stereotype.Repository;

import java.util.List;
import java.util.Optional;

@Repository
public interface RagIndexStatusRepository extends JpaRepository<RagIndexStatus, Long> {

    Optional<RagIndexStatus> findByProjectId(Long projectId);

    Optional<RagIndexStatus> findByWorkspaceNameAndProjectName(String workspaceName, String projectName);

    List<RagIndexStatus> findByStatus(RagIndexingStatus status);

    @Query("SELECT r FROM RagIndexStatus r WHERE r.workspaceName = :workspace AND r.status = :status")
    List<RagIndexStatus> findByWorkspaceAndStatus(@Param("workspace") String workspace,
                                                   @Param("status") RagIndexingStatus status);

    @Query("SELECT CASE WHEN COUNT(r) > 0 THEN true ELSE false END FROM RagIndexStatus r " +
           "WHERE r.project.id = :projectId AND r.status = 'INDEXED'")
    boolean isProjectIndexed(@Param("projectId") Long projectId);
}

