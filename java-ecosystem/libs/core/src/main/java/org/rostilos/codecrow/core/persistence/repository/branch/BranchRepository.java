package org.rostilos.codecrow.core.persistence.repository.branch;

import org.rostilos.codecrow.core.model.branch.Branch;
import org.rostilos.codecrow.core.model.branch.BranchHealthStatus;
import org.springframework.data.jpa.repository.JpaRepository;
import org.springframework.data.jpa.repository.Query;
import org.springframework.data.repository.query.Param;
import org.springframework.stereotype.Repository;

import java.util.Optional;
import java.util.List;

@Repository
public interface BranchRepository extends JpaRepository<Branch, Long> {

    interface StaleRetryCandidate {
        Long getBranchId();
        Long getProjectId();
        String getBranchName();
        String getCommitHash();
        int getConsecutiveFailures();
        java.time.OffsetDateTime getLastHealthCheckAt();
        boolean getBranchAnalysisEnabled();
    }

    Optional<Branch> findByProjectIdAndBranchName(Long projectId, String branchName);

    Optional<Branch> findByProjectIdAndCommitHash(Long projectId, String commitHash);

    List<Branch> findByProjectId(Long projectId);

    Optional<Branch> findFirstByProjectIdOrderByIdAsc(Long projectId);

    void deleteByProjectId(Long projectId);

    @Query("SELECT b FROM Branch b LEFT JOIN FETCH b.issues WHERE b.id = :id")
    Optional<Branch> findByIdWithIssues(@Param("id") Long id);

    /**
     * Find branches in STALE health status that are eligible for automated retry.
     * Eagerly fetches the project to avoid N+1 queries in the scheduler.
     */
    @Query("SELECT b FROM Branch b JOIN FETCH b.project WHERE b.healthStatus = :status")
    List<Branch> findByHealthStatusWithProject(@Param("status") BranchHealthStatus status);

    /**
     * Materializes only scheduler inputs so the repository's short read
     * transaction is closed before a retry performs VCS, RAG, or AI work.
     */
    @Query("""
            SELECT b.id AS branchId,
                   b.project.id AS projectId,
                   b.branchName AS branchName,
                   b.commitHash AS commitHash,
                   b.consecutiveFailures AS consecutiveFailures,
                   b.lastHealthCheckAt AS lastHealthCheckAt,
                   b.project.branchAnalysisEnabled AS branchAnalysisEnabled
            FROM Branch b
            WHERE b.healthStatus = :status
            """)
    List<StaleRetryCandidate> findStaleRetryCandidates(
            @Param("status") BranchHealthStatus status);
}
