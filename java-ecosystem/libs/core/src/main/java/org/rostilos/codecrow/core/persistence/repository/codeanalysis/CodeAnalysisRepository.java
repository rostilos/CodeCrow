package org.rostilos.codecrow.core.persistence.repository.codeanalysis;
import org.rostilos.codecrow.core.model.codeanalysis.CodeAnalysis;
import org.rostilos.codecrow.core.model.codeanalysis.AnalysisType;
import org.rostilos.codecrow.core.model.codeanalysis.AnalysisStatus;
import org.springframework.data.domain.Page;
import org.springframework.data.domain.Pageable;
import org.springframework.data.jpa.repository.JpaRepository;
import org.springframework.data.jpa.repository.Query;
import org.springframework.data.repository.query.Param;
import org.springframework.stereotype.Repository;

import java.time.OffsetDateTime;
import java.util.List;
import java.util.Optional;

@Repository
public interface CodeAnalysisRepository extends JpaRepository<CodeAnalysis, Long> {

    List<CodeAnalysis> findByProjectIdOrderByCreatedAtDesc(Long projectId);

    List<CodeAnalysis> findByProjectIdAndAnalysisTypeOrderByCreatedAtDesc(Long projectId, AnalysisType analysisType);

    List<CodeAnalysis> findByProjectIdAndStatusOrderByCreatedAtDesc(Long projectId, AnalysisStatus status);

    Optional<CodeAnalysis> findByProjectIdAndPrNumber(Long projectId, Long prNumber);

    Optional<CodeAnalysis> findByProjectIdAndPrNumberAndPrVersion(Long projectId, Long prNumber, int prVersion);

    @org.springframework.data.jpa.repository.EntityGraph(attributePaths = {
            "issues",
            "project",
            "project.workspace",
            "project.vcsBinding",
            "project.vcsBinding.vcsConnection",
            "project.aiBinding"
    })
    Optional<CodeAnalysis> findByProjectIdAndCommitHashAndPrNumber(Long projectId, String commitHash, Long prNumber);

    Optional<CodeAnalysis> findByProjectIdAndCommitHash(Long projectId, String commitHash);

    List<CodeAnalysis> findByProjectIdAndBranchName(Long projectId, String branchName);

    @Query("SELECT ca FROM CodeAnalysis ca WHERE ca.project.id = :projectId " +
            "AND ca.createdAt BETWEEN :startDate AND :endDate " +
            "ORDER BY ca.createdAt DESC")
    List<CodeAnalysis> findByProjectIdAndDateRange(@Param("projectId") Long projectId,
                                                   @Param("startDate") OffsetDateTime startDate,
                                                   @Param("endDate") OffsetDateTime endDate);

    @Query("SELECT ca FROM CodeAnalysis ca WHERE ca.project.id = :projectId " +
            "AND ca.highSeverityCount > 0 ORDER BY ca.createdAt DESC")
    List<CodeAnalysis> findByProjectIdWithHighSeverityIssues(@Param("projectId") Long projectId);

    @Query("SELECT COUNT(ca) FROM CodeAnalysis ca WHERE ca.project.id = :projectId")
    long countByProjectId(@Param("projectId") Long projectId);

    @Query("SELECT AVG(ca.totalIssues) FROM CodeAnalysis ca WHERE ca.project.id = :projectId")
    Double getAverageIssuesPerAnalysis(@Param("projectId") Long projectId);

    @org.springframework.data.jpa.repository.EntityGraph(attributePaths = {
            "issues",
            "project",
            "project.workspace",
            "project.vcsBinding",
            "project.vcsBinding.vcsConnection",
            "project.aiBinding",
            "project.aiBinding.aiConnection"
    })
    @Query("SELECT ca FROM CodeAnalysis ca WHERE ca.project.id = :projectId " +
            "ORDER BY ca.createdAt DESC LIMIT 1")
    Optional<CodeAnalysis> findLatestByProjectId(@Param("projectId") Long projectId);

    void deleteByProjectId(Long projectId);

    @Query("SELECT COALESCE(MAX(a.prVersion), 0) FROM CodeAnalysis a WHERE a.project.id = :projectId AND a.prNumber = :prNumber")
    Optional<Integer> findMaxPrVersion(@Param("projectId") Long projectId, @Param("prNumber") Long prNumber);

    @org.springframework.data.jpa.repository.EntityGraph(attributePaths = {
            "issues",
            "project",
            "project.workspace",
            "project.vcsBinding",
            "project.vcsBinding.vcsConnection",
            "project.aiBinding",
            "project.aiBinding.aiConnection"
    })
    @Query("SELECT a FROM CodeAnalysis a WHERE a.project.id = :projectId AND a.prNumber = :prNumber AND a.prVersion = (SELECT MAX(b.prVersion) FROM CodeAnalysis b WHERE b.project.id = :projectId AND b.prNumber = :prNumber)")
    Optional<CodeAnalysis> findByProjectIdAndPrNumberWithMaxPrVersion(@Param("projectId") Long projectId, @Param("prNumber") Long prNumber);

    /**
     * Paginated search for analyses with optional filters.
     * Handles filtering at the database level for better performance.
     */
    @Query("SELECT ca FROM CodeAnalysis ca WHERE ca.project.id = :projectId " +
            "AND (:prNumber IS NULL OR ca.prNumber = :prNumber) " +
            "AND (:status IS NULL OR ca.status = :status)")
    Page<CodeAnalysis> searchAnalyses(
            @Param("projectId") Long projectId,
            @Param("prNumber") Long prNumber,
            @Param("status") AnalysisStatus status,
            Pageable pageable);

    /**
     * Find analysis by ID with issues eagerly loaded for quality gate evaluation.
     * Note: Quality gate conditions are fetched separately to avoid MultipleBagFetchException.
     */
    @org.springframework.data.jpa.repository.EntityGraph(attributePaths = {
            "issues",
            "project",
            "project.workspace"
    })
    @Query("SELECT ca FROM CodeAnalysis ca WHERE ca.id = :id")
    Optional<CodeAnalysis> findByIdWithIssues(@Param("id") Long id);

    /**
     * Find the most recent ACCEPTED analysis for a project with the same diff fingerprint.
     * Used for content-based cache: reuse analysis when the same code changes appear in a different PR.
     */
    @org.springframework.data.jpa.repository.EntityGraph(attributePaths = {
            "issues",
            "project",
            "project.workspace",
            "project.vcsBinding",
            "project.vcsBinding.vcsConnection",
            "project.aiBinding"
    })
    @Query("SELECT ca FROM CodeAnalysis ca WHERE ca.project.id = :projectId " +
            "AND ca.diffFingerprint = :diffFingerprint " +
            "AND ca.status = org.rostilos.codecrow.core.model.codeanalysis.AnalysisStatus.ACCEPTED " +
            "ORDER BY ca.createdAt DESC LIMIT 1")
    Optional<CodeAnalysis> findTopByProjectIdAndDiffFingerprint(
            @Param("projectId") Long projectId,
            @Param("diffFingerprint") String diffFingerprint);

    /**
     * Find the most recent ACCEPTED analysis for a project + commit hash (any PR number).
     * Fallback cache for close/reopen scenarios where the same commit gets a new PR number.
     */
    @org.springframework.data.jpa.repository.EntityGraph(attributePaths = {
            "issues",
            "project",
            "project.workspace",
            "project.vcsBinding",
            "project.vcsBinding.vcsConnection",
            "project.aiBinding"
    })
    @Query("SELECT ca FROM CodeAnalysis ca WHERE ca.project.id = :projectId " +
            "AND ca.commitHash = :commitHash " +
            "AND ca.status = org.rostilos.codecrow.core.model.codeanalysis.AnalysisStatus.ACCEPTED " +
            "ORDER BY ca.createdAt DESC LIMIT 1")
    Optional<CodeAnalysis> findTopByProjectIdAndCommitHash(
            @Param("projectId") Long projectId,
            @Param("commitHash") String commitHash);

    /**
     * Find all analyses for a PR across all versions, ordered by version descending.
     * Used to provide LLM with full issue history including resolved issues.
     */
    @org.springframework.data.jpa.repository.EntityGraph(attributePaths = {"issues"})
    @Query("SELECT ca FROM CodeAnalysis ca WHERE ca.project.id = :projectId AND ca.prNumber = :prNumber ORDER BY ca.prVersion DESC")
    List<CodeAnalysis> findAllByProjectIdAndPrNumberOrderByPrVersionDesc(@Param("projectId") Long projectId, @Param("prNumber") Long prNumber);
}
