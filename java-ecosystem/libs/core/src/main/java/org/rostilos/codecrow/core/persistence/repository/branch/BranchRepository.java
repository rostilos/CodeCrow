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

    Optional<Branch> findByProjectIdAndBranchName(Long projectId, String branchName);

    Optional<Branch> findByProjectIdAndCommitHash(Long projectId, String commitHash);

    List<Branch> findByProjectId(Long projectId);

    void deleteByProjectId(Long projectId);

    @Query("SELECT b FROM Branch b LEFT JOIN FETCH b.issues WHERE b.id = :id")
    Optional<Branch> findByIdWithIssues(@Param("id") Long id);

    /**
     * Find branches in STALE health status that are eligible for automated retry.
     * Eagerly fetches the project to avoid N+1 queries in the scheduler.
     */
    @Query("SELECT b FROM Branch b JOIN FETCH b.project WHERE b.healthStatus = :status")
    List<Branch> findByHealthStatusWithProject(@Param("status") BranchHealthStatus status);
}
