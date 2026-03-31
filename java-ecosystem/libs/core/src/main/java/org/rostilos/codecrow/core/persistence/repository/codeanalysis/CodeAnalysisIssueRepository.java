package org.rostilos.codecrow.core.persistence.repository.codeanalysis;

import org.rostilos.codecrow.core.model.codeanalysis.CodeAnalysisIssue;
import org.rostilos.codecrow.core.model.codeanalysis.IssueSeverity;
import org.springframework.data.jpa.repository.JpaRepository;
import org.springframework.data.jpa.repository.Query;
import org.springframework.data.repository.query.Param;
import org.springframework.stereotype.Repository;

import java.util.List;

@Repository
public interface CodeAnalysisIssueRepository extends JpaRepository<CodeAnalysisIssue, Long> {

    List<CodeAnalysisIssue> findByAnalysisIdOrderBySeverityDescLineNumberAsc(Long analysisId);

    List<CodeAnalysisIssue> findByAnalysisIdAndSeverityOrderByLineNumberAsc(Long analysisId, IssueSeverity severity);

    List<CodeAnalysisIssue> findByAnalysisIdAndResolvedOrderByLineNumberAsc(Long analysisId, boolean resolved);

    @org.springframework.data.jpa.repository.EntityGraph(attributePaths = {
            "analysis",
            "analysis.project",
            "analysis.project.workspace",
            "analysis.project.vcsBinding",
            "analysis.project.vcsBinding.vcsConnection",
            "analysis.project.aiBinding",
            "analysis.project.aiBinding.aiConnection"
    })
    @Query("SELECT cai FROM CodeAnalysisIssue cai WHERE cai.analysis.project.id = :projectId " +
            "AND cai.filePath = :filePath ORDER BY cai.analysis.createdAt DESC")
    List<CodeAnalysisIssue> findByProjectIdAndFilePath(@Param("projectId") Long projectId,
                                                       @Param("filePath") String filePath);

    @Query("SELECT COUNT(cai) FROM CodeAnalysisIssue cai WHERE cai.analysis.project.id = :projectId " +
            "AND cai.severity = :severity AND cai.resolved = false")
    long countByProjectIdAndSeverity(@Param("projectId") Long projectId,
                                     @Param("severity") IssueSeverity severity);

    @Query("SELECT cai.filePath, COUNT(cai) FROM CodeAnalysisIssue cai " +
            "WHERE cai.analysis.project.id = :projectId " +
            "GROUP BY cai.filePath ORDER BY COUNT(cai) DESC")
    List<Object[]> findMostProblematicFilesByProjectId(@Param("projectId") Long projectId);

    @Query("SELECT COUNT(DISTINCT cai.filePath) FROM CodeAnalysisIssue cai " +
            "WHERE cai.analysis.id = :analysisId AND cai.filePath IS NOT NULL")
    int countDistinctFilePathsByAnalysisId(@Param("analysisId") Long analysisId);

    void deleteByAnalysisId(Long analysisId);

    @org.springframework.data.jpa.repository.EntityGraph(attributePaths = {
            "analysis",
            "analysis.project",
            "analysis.project.workspace"
    })
    @Query("SELECT cai FROM CodeAnalysisIssue cai WHERE cai.id = :id")
    java.util.Optional<CodeAnalysisIssue> findByIdWithAnalysis(@Param("id") Long id);

    /**
     * Find all issues for a specific file across ALL analyses on the given branch.
     * Used by the source code viewer to show every issue for a file, not just those
     * from one particular analysis.
     * Also used by BranchIssueMappingService to scope issue mapping to the correct branch.
     */
    @org.springframework.data.jpa.repository.EntityGraph(attributePaths = {
            "analysis"
    })
    @Query("SELECT cai FROM CodeAnalysisIssue cai " +
            "WHERE cai.analysis.project.id = :projectId " +
            "AND cai.analysis.branchName = :branchName " +
            "AND cai.filePath = :filePath " +
            "ORDER BY cai.lineNumber ASC, cai.analysis.createdAt DESC")
    List<CodeAnalysisIssue> findByProjectIdAndBranchNameAndFilePath(
            @Param("projectId") Long projectId,
            @Param("branchName") String branchName,
            @Param("filePath") String filePath);

    /**
     * Find all issues across ALL analyses on the given branch.
     * Used by the source code viewer to build accurate issue counts per file.
     */
    @Query("SELECT cai FROM CodeAnalysisIssue cai " +
            "WHERE cai.analysis.project.id = :projectId " +
            "AND cai.analysis.branchName = :branchName " +
            "ORDER BY cai.filePath ASC, cai.lineNumber ASC")
    List<CodeAnalysisIssue> findByProjectIdAndBranchName(
            @Param("projectId") Long projectId,
            @Param("branchName") String branchName);

    // ── PR-level issue queries (join through analysis.prNumber) ──────────────

    /**
     * Find all issues across all analyses for a specific PR.
     * Used by the PR-level source code viewer to show issue counts per file.
     */
    @Query("SELECT cai FROM CodeAnalysisIssue cai " +
            "WHERE cai.analysis.project.id = :projectId " +
            "AND cai.analysis.prNumber = :prNumber " +
            "ORDER BY cai.filePath ASC, cai.lineNumber ASC")
    List<CodeAnalysisIssue> findByProjectIdAndPrNumber(
            @Param("projectId") Long projectId,
            @Param("prNumber") Long prNumber);

    /**
     * Find all issues for a specific file across all analyses for a PR.
     * Used by the PR-level source code viewer to overlay inline issue annotations.
     */
    @Query("SELECT cai FROM CodeAnalysisIssue cai " +
            "WHERE cai.analysis.project.id = :projectId " +
            "AND cai.analysis.prNumber = :prNumber " +
            "AND cai.filePath = :filePath " +
            "ORDER BY cai.lineNumber ASC, cai.analysis.createdAt DESC")
    List<CodeAnalysisIssue> findByProjectIdAndPrNumberAndFilePath(
            @Param("projectId") Long projectId,
            @Param("prNumber") Long prNumber,
            @Param("filePath") String filePath);

    /**
     * Find issues for the <b>latest PR version only</b>.
     * Scopes to the analysis with MAX(prVersion) for this PR number.
     * Used by the PR source code viewer (file tree counts).
     */
    @Query("SELECT cai FROM CodeAnalysisIssue cai " +
            "WHERE cai.analysis.project.id = :projectId " +
            "AND cai.analysis.prNumber = :prNumber " +
            "AND cai.analysis.prVersion = (" +
            "  SELECT MAX(a.prVersion) FROM CodeAnalysis a " +
            "  WHERE a.project.id = :projectId AND a.prNumber = :prNumber" +
            ") " +
            "ORDER BY cai.filePath ASC, cai.lineNumber ASC")
    List<CodeAnalysisIssue> findByProjectIdAndPrNumberLatestVersion(
            @Param("projectId") Long projectId,
            @Param("prNumber") Long prNumber);

    /**
     * Find issues for a specific file in the <b>latest PR version only</b>.
     * Scopes to the analysis with MAX(prVersion) for this PR number.
     * Used by the PR source code viewer (inline issue annotations).
     */
    @Query("SELECT cai FROM CodeAnalysisIssue cai " +
            "WHERE cai.analysis.project.id = :projectId " +
            "AND cai.analysis.prNumber = :prNumber " +
            "AND cai.filePath = :filePath " +
            "AND cai.analysis.prVersion = (" +
            "  SELECT MAX(a.prVersion) FROM CodeAnalysis a " +
            "  WHERE a.project.id = :projectId AND a.prNumber = :prNumber" +
            ") " +
            "ORDER BY cai.lineNumber ASC")
    List<CodeAnalysisIssue> findByProjectIdAndPrNumberAndFilePathLatestVersion(
            @Param("projectId") Long projectId,
            @Param("prNumber") Long prNumber,
            @Param("filePath") String filePath);

    // ── Aggregate queries (for analytics — avoid loading full entity graphs) ──

    @Query("SELECT COUNT(cai) FROM CodeAnalysisIssue cai " +
            "WHERE cai.analysis.project.id = :projectId AND cai.resolved = true")
    long countResolvedByProjectId(@Param("projectId") Long projectId);

    @Query("SELECT COUNT(cai) FROM CodeAnalysisIssue cai " +
            "WHERE cai.analysis.project.id = :projectId AND cai.resolved = false")
    long countOpenByProjectId(@Param("projectId") Long projectId);

    /**
     * Open issue count grouped by issueCategory.
     * Returns rows of [IssueCategory, Long count].
     */
    @Query("SELECT cai.issueCategory, COUNT(cai) FROM CodeAnalysisIssue cai " +
            "WHERE cai.analysis.project.id = :projectId AND cai.resolved = false " +
            "GROUP BY cai.issueCategory")
    List<Object[]> countOpenByProjectIdGroupedByCategory(@Param("projectId") Long projectId);

    /**
     * Highest severity per file path for a set of files.
     * Returns rows of [String filePath, IssueSeverity severity] — one per (file, severity) pair.
     * Caller groups by file and picks the highest severity.
     */
    @Query("SELECT DISTINCT cai.filePath, cai.severity FROM CodeAnalysisIssue cai " +
            "WHERE cai.analysis.project.id = :projectId AND cai.resolved = false " +
            "AND cai.filePath IN :filePaths")
    List<Object[]> findSeveritiesByProjectIdAndFilePaths(
            @Param("projectId") Long projectId,
            @Param("filePaths") List<String> filePaths);
}
