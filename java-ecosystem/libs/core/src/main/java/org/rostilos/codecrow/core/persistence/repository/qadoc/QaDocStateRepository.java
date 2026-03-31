package org.rostilos.codecrow.core.persistence.repository.qadoc;

import org.rostilos.codecrow.core.model.qadoc.QaDocState;
import org.springframework.data.jpa.repository.JpaRepository;
import org.springframework.data.jpa.repository.Modifying;
import org.springframework.data.jpa.repository.Query;
import org.springframework.data.repository.query.Param;
import org.springframework.stereotype.Repository;

import java.util.Optional;

@Repository
public interface QaDocStateRepository extends JpaRepository<QaDocState, Long> {

    Optional<QaDocState> findByProjectIdAndTaskId(Long projectId, String taskId);

    boolean existsByProjectIdAndTaskId(Long projectId, String taskId);

    @Modifying
    @Query("DELETE FROM QaDocState q WHERE q.project.id = :projectId")
    void deleteByProjectId(@Param("projectId") Long projectId);
}
