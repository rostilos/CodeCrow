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
    
    List<CodeAnalysis> findByProjectIdAndCommitHashIn(Long projectId, List<String> commitHashes);

    List<CodeAnalysis> findByProjectIdAndBranchName(Long projectId, String branchName);

    /**
     * Find the most recent analysis for a project + branch name.
     * Used by the branch-level source viewer to load the latest analyzed files.
     */
    @Query("SELECT ca FROM CodeAnalysis ca WHERE ca.id = " +
            "(SELECT ca2.id FROM CodeAnalysis ca2 WHERE ca2.project.id = :projectId " +
            "AND ca2.branchName = :branchName " +
            "ORDER BY ca2.createdAt DESC LIMIT 1)")
    Optional<CodeAnalysis> findLatestByProjectIdAndBranchName(
            @Param("projectId") Long projectId,
            @Param("branchName") String branchName);

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
    @Query("SELECT ca FROM CodeAnalysis ca WHERE ca.id = " +
            "(SELECT ca2.id FROM CodeAnalysis ca2 WHERE ca2.project.id = :projectId " +
            "ORDER BY ca2.createdAt DESC LIMIT 1)")
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
    @Query("SELECT ca FROM CodeAnalysis ca WHERE ca.id = " +
            "(SELECT ca2.id FROM CodeAnalysis ca2 WHERE ca2.project.id = :projectId " +
            "AND ca2.diffFingerprint = :diffFingerprint " +
            "AND ca2.status = org.rostilos.codecrow.core.model.codeanalysis.AnalysisStatus.ACCEPTED " +
            "ORDER BY ca2.createdAt DESC LIMIT 1)")
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
    @Query("SELECT ca FROM CodeAnalysis ca WHERE ca.id = " +
            "(SELECT ca2.id FROM CodeAnalysis ca2 WHERE ca2.project.id = :projectId " +
            "AND ca2.commitHash = :commitHash " +
            "AND ca2.status = org.rostilos.codecrow.core.model.codeanalysis.AnalysisStatus.ACCEPTED " +
            "ORDER BY ca2.createdAt DESC LIMIT 1)")
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

    // ── Batch / optimized queries (no eager loading of issues) ─────────

    /**
     * Fetch the latest-version analysis for EACH PR in a project — one row per PR number.
     * Used by the PR list endpoints to avoid N+1 queries.
     * No EntityGraph → issues/project relations are NOT eagerly loaded.
     */
    @Query("SELECT a FROM CodeAnalysis a WHERE a.project.id = :projectId " +
            "AND a.prNumber IS NOT NULL " +
            "AND a.prVersion = (SELECT MAX(b.prVersion) FROM CodeAnalysis b " +
            "WHERE b.project.id = :projectId AND b.prNumber = a.prNumber)")
    List<CodeAnalysis> findLatestAnalysisPerPrNumber(@Param("projectId") Long projectId);

    /**
     * Same as above but limited to a specific set of PR numbers (for paginated PR lists).
     */
    @Query("SELECT a FROM CodeAnalysis a WHERE a.project.id = :projectId " +
            "AND a.prNumber IN :prNumbers " +
            "AND a.prVersion = (SELECT MAX(b.prVersion) FROM CodeAnalysis b " +
            "WHERE b.project.id = :projectId AND b.prNumber = a.prNumber)")
    List<CodeAnalysis> findLatestAnalysisForPrNumbers(
            @Param("projectId") Long projectId,
            @Param("prNumbers") List<Long> prNumbers);

    /**
     * Paginated analysis history — lightweight, no issues eager-loaded.
     * Useful for recent-analyses lists and trend calculations.
     */
    Page<CodeAnalysis> findByProjectIdOrderByCreatedAtDesc(Long projectId, Pageable pageable);

    /**
     * Paginated analysis history filtered by branch.
     */
    Page<CodeAnalysis> findByProjectIdAndBranchNameOrderByCreatedAtDesc(
            Long projectId, String branchName, Pageable pageable);

    /**
     * Latest analysis per branch name — one row per branch.
     * Used by the detailed-stats branchStats map.
     */
    @Query("SELECT a FROM CodeAnalysis a WHERE a.project.id = :projectId " +
            "AND a.id IN (SELECT MAX(b.id) FROM CodeAnalysis b " +
            "WHERE b.project.id = :projectId GROUP BY b.branchName)")
    List<CodeAnalysis> findLatestAnalysisPerBranch(@Param("projectId") Long projectId);

    /**
     * Analyses within a timeframe — lightweight, for trend calculation.
     */
    @Query("SELECT ca FROM CodeAnalysis ca WHERE ca.project.id = :projectId " +
            "AND ca.createdAt >= :cutoff ORDER BY ca.createdAt ASC")
    List<CodeAnalysis> findByProjectIdAndCreatedAtAfter(
            @Param("projectId") Long projectId,
            @Param("cutoff") OffsetDateTime cutoff);

    /**
     * Analyses within a timeframe filtered by branch.
     */
    @Query("SELECT ca FROM CodeAnalysis ca WHERE ca.project.id = :projectId " +
            "AND ca.branchName = :branchName " +
            "AND ca.createdAt >= :cutoff ORDER BY ca.createdAt ASC")
    List<CodeAnalysis> findByProjectIdAndBranchNameAndCreatedAtAfter(
            @Param("projectId") Long projectId,
            @Param("branchName") String branchName,
            @Param("cutoff") OffsetDateTime cutoff);
}
