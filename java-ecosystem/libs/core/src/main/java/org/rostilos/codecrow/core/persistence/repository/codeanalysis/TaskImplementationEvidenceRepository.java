package org.rostilos.codecrow.core.persistence.repository.codeanalysis;

import org.rostilos.codecrow.core.model.codeanalysis.TaskImplementationEvidence;
import org.springframework.data.domain.Pageable;
import org.springframework.data.jpa.repository.JpaRepository;
import org.springframework.data.jpa.repository.Query;
import org.springframework.data.repository.query.Param;
import org.springframework.stereotype.Repository;

import java.util.Collection;
import java.util.List;

@Repository
public interface TaskImplementationEvidenceRepository
        extends JpaRepository<TaskImplementationEvidence, Long> {

    @Query("SELECT e.contentFingerprint FROM TaskImplementationEvidence e " +
            "WHERE e.analysis.id = :analysisId")
    List<String> findFingerprintsByAnalysisId(@Param("analysisId") Long analysisId);

    @Query("SELECT e FROM TaskImplementationEvidence e " +
            "WHERE e.analysis.id IN :analysisIds " +
            "ORDER BY e.analysis.id ASC, e.id ASC")
    List<TaskImplementationEvidence> findByAnalysisIds(
            @Param("analysisIds") Collection<Long> analysisIds);

    @Query("SELECT e FROM TaskImplementationEvidence e " +
            "WHERE e.project.id = :projectId " +
            "AND e.taskId = :taskId " +
            "AND (:excludedPrNumber IS NULL OR e.prNumber <> :excludedPrNumber) " +
            "ORDER BY e.createdAt DESC, e.id DESC")
    List<TaskImplementationEvidence> findForTaskHistory(
            @Param("projectId") Long projectId,
            @Param("taskId") String taskId,
            @Param("excludedPrNumber") Long excludedPrNumber,
            Pageable pageable);
}
