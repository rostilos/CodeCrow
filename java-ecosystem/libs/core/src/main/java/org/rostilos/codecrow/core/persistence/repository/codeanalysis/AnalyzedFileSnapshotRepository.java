package org.rostilos.codecrow.core.persistence.repository.codeanalysis;

import org.rostilos.codecrow.core.model.codeanalysis.AnalyzedFileSnapshot;
import org.springframework.data.jpa.repository.JpaRepository;
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
}
