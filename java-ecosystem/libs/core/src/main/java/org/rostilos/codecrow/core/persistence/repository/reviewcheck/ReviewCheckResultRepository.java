package org.rostilos.codecrow.core.persistence.repository.reviewcheck;

import org.rostilos.codecrow.core.model.reviewcheck.ReviewCheckResult;
import org.springframework.data.jpa.repository.EntityGraph;
import org.springframework.data.jpa.repository.JpaRepository;
import org.springframework.data.jpa.repository.Query;
import org.springframework.data.repository.query.Param;

import java.util.List;

public interface ReviewCheckResultRepository extends JpaRepository<ReviewCheckResult, Long> {
    @EntityGraph(attributePaths = "findings")
    List<ReviewCheckResult> findByAnalysis_Project_IdAndAnalysis_IdOrderByCheckId(Long projectId, Long analysisId);

    @EntityGraph(attributePaths = "findings")
    @Query("""
        SELECT result FROM ReviewCheckResult result
        WHERE result.analysis.project.id = :projectId AND result.analysis.prNumber = :prNumber
          AND result.analysis.prVersion = (
            SELECT MAX(prior.analysis.prVersion) FROM ReviewCheckResult prior
            WHERE prior.analysis.project.id = :projectId AND prior.analysis.prNumber = :prNumber
              AND prior.checkId = result.checkId AND prior.analysis.prVersion < :version)
        ORDER BY result.checkId
        """)
    List<ReviewCheckResult> findPrevious(@Param("projectId") Long projectId, @Param("prNumber") Long prNumber,
                                       @Param("version") int version);
}
