package org.rostilos.codecrow.filecontent.persistence;

import org.rostilos.codecrow.filecontent.model.AnalyzedFileSnapshot;
import org.springframework.data.jpa.repository.JpaRepository;
import org.springframework.data.jpa.repository.Modifying;
import org.springframework.data.jpa.repository.Query;
import org.springframework.data.repository.query.Param;
import org.springframework.stereotype.Repository;

import java.util.List;
import java.util.Optional;

@Repository
public interface AnalyzedFileSnapshotRepository extends JpaRepository<AnalyzedFileSnapshot, Long> {

    /**
     * Find all file snapshots for a given analysis.
     */
    List<AnalyzedFileSnapshot> findByAnalysisId(Long analysisId);

    /**
     * Find a specific file snapshot for a given analysis and file path.
     */
    Optional<AnalyzedFileSnapshot> findByAnalysisIdAndFilePath(Long analysisId, String filePath);

    /**
     * Find all file snapshots for a given analysis, eagerly fetching content.
     */
    @Query("SELECT s FROM AnalyzedFileSnapshot s " +
           "JOIN FETCH s.fileContent " +
           "WHERE s.analysis.id = :analysisId")
    List<AnalyzedFileSnapshot> findByAnalysisIdWithContent(@Param("analysisId") Long analysisId);

    /**
     * Find a specific snapshot with its content eagerly loaded.
     */
    @Query("SELECT s FROM AnalyzedFileSnapshot s " +
           "JOIN FETCH s.fileContent " +
           "WHERE s.analysis.id = :analysisId AND s.filePath = :filePath")
    Optional<AnalyzedFileSnapshot> findByAnalysisIdAndFilePathWithContent(
            @Param("analysisId") Long analysisId,
            @Param("filePath") String filePath);

    /**
     * Delete all snapshots for a given analysis.
     */
    void deleteByAnalysisId(Long analysisId);

    // ── PR-level queries ─────────────────────────────────────────────────

    /**
     * Find all file snapshots accumulated for a pull request.
     */
    List<AnalyzedFileSnapshot> findByPullRequestId(Long pullRequestId);

    /**
     * Find a specific snapshot for a pull request and file path.
     */
    Optional<AnalyzedFileSnapshot> findByPullRequestIdAndFilePath(Long pullRequestId, String filePath);

    /**
     * Find all file snapshots for a pull request, eagerly fetching content.
     */
    @Query("SELECT s FROM AnalyzedFileSnapshot s " +
           "JOIN FETCH s.fileContent " +
           "WHERE s.pullRequest.id = :prId")
    List<AnalyzedFileSnapshot> findByPullRequestIdWithContent(@Param("prId") Long prId);

    /**
     * Find a specific PR snapshot with its content eagerly loaded.
     */
    @Query("SELECT s FROM AnalyzedFileSnapshot s " +
           "JOIN FETCH s.fileContent " +
           "WHERE s.pullRequest.id = :prId AND s.filePath = :filePath")
    Optional<AnalyzedFileSnapshot> findByPullRequestIdAndFilePathWithContent(
            @Param("prId") Long prId,
            @Param("filePath") String filePath);

    // ── Branch-level aggregated queries ──────────────────────────────────

    /**
     * Get the latest snapshot for each file path across ALL analyses on a branch.
     * Uses PostgreSQL DISTINCT ON to pick the most recent snapshot per file path.
     * Returns metadata only (no content join).
     *
     * @deprecated Prefer {@link #findByBranchId(Long)} which uses the direct branch_id FK.
     */
    @Deprecated
    @Query(value = "SELECT DISTINCT ON (s.file_path) s.* " +
           "FROM analyzed_file_snapshot s " +
           "INNER JOIN code_analysis a ON s.analysis_id = a.id " +
           "WHERE a.project_id = :projectId AND a.target_branch_name = :branchName " +
           "ORDER BY s.file_path, a.created_at DESC",
           nativeQuery = true)
    List<AnalyzedFileSnapshot> findLatestSnapshotsByBranch(
            @Param("projectId") Long projectId,
            @Param("branchName") String branchName);

    /**
     * Get the latest snapshot with content for a specific file on a branch.
     *
     * @deprecated Prefer {@link #findByBranchIdAndFilePath(Long, String)} which uses the direct branch_id FK.
     */
    @Deprecated
    @Query(value = "SELECT s.* " +
           "FROM analyzed_file_snapshot s " +
           "INNER JOIN code_analysis a ON s.analysis_id = a.id " +
           "WHERE a.project_id = :projectId AND a.target_branch_name = :branchName " +
           "AND s.file_path = :filePath " +
           "ORDER BY a.created_at DESC LIMIT 1",
           nativeQuery = true)
    Optional<AnalyzedFileSnapshot> findLatestSnapshotByBranchAndFilePath(
            @Param("projectId") Long projectId,
            @Param("branchName") String branchName,
            @Param("filePath") String filePath);

    // ── Branch FK-based queries (new: direct branch_id column) ───────────

    /**
     * Find all file snapshots for a branch using the direct branch_id FK.
     * Each file path has exactly one snapshot (upsert model).
     */
    List<AnalyzedFileSnapshot> findByBranchId(Long branchId);

    /**
     * Find a specific file snapshot for a branch and file path.
     */
    Optional<AnalyzedFileSnapshot> findByBranchIdAndFilePath(Long branchId, String filePath);

    /**
     * Find all file snapshots for a branch with content eagerly loaded.
     */
    @Query("SELECT s FROM AnalyzedFileSnapshot s " +
           "JOIN FETCH s.fileContent " +
           "WHERE s.branch.id = :branchId")
    List<AnalyzedFileSnapshot> findByBranchIdWithContent(@Param("branchId") Long branchId);

    /**
     * Find a specific branch snapshot with its content eagerly loaded.
     */
    @Query("SELECT s FROM AnalyzedFileSnapshot s " +
           "JOIN FETCH s.fileContent " +
           "WHERE s.branch.id = :branchId AND s.filePath = :filePath")
    Optional<AnalyzedFileSnapshot> findByBranchIdAndFilePathWithContent(
            @Param("branchId") Long branchId,
            @Param("filePath") String filePath);

    // ── Source availability queries ──────────────────────────────────────

    /**
     * Get all branch names that have at least one file snapshot stored.
     */
    @Query(value = "SELECT DISTINCT a.target_branch_name " +
           "FROM analyzed_file_snapshot s " +
           "INNER JOIN code_analysis a ON s.analysis_id = a.id " +
           "WHERE a.project_id = :projectId AND a.target_branch_name IS NOT NULL",
           nativeQuery = true)
    List<String> findBranchNamesWithSnapshots(@Param("projectId") Long projectId);

    /**
     * Get all PR numbers that have at least one file snapshot stored.
     */
    @Query(value = "SELECT DISTINCT pr.pr_number " +
           "FROM analyzed_file_snapshot s " +
           "INNER JOIN pull_request pr ON s.pull_request_id = pr.id " +
           "WHERE pr.project_id = :projectId",
           nativeQuery = true)
    List<Long> findPrNumbersWithSnapshots(@Param("projectId") Long projectId);

    // ── Project-level bulk delete (for project deletion) ─────────────────

    /**
     * Delete all snapshots whose analysis belongs to the given project.
     * Must be called before deleting code_analysis rows.
     */
    @Modifying
    @Query("DELETE FROM AnalyzedFileSnapshot s WHERE s.analysis.id IN " +
           "(SELECT a.id FROM CodeAnalysis a WHERE a.project.id = :projectId)")
    void deleteByProjectIdViaAnalysis(@Param("projectId") Long projectId);

    /**
     * Delete all snapshots whose pull request belongs to the given project.
     * Must be called before deleting pull_request rows.
     */
    @Modifying
    @Query("DELETE FROM AnalyzedFileSnapshot s WHERE s.pullRequest.id IN " +
           "(SELECT pr.id FROM PullRequest pr WHERE pr.project.id = :projectId)")
    void deleteByProjectIdViaPullRequest(@Param("projectId") Long projectId);

    /**
     * Delete all snapshots whose branch belongs to the given project.
     * Must be called before deleting branch rows.
     */
    @Modifying
    @Query("DELETE FROM AnalyzedFileSnapshot s WHERE s.branch.id IN " +
           "(SELECT b.id FROM Branch b WHERE b.project.id = :projectId)")
    void deleteByProjectIdViaBranch(@Param("projectId") Long projectId);
}
