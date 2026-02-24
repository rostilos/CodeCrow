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
     */
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
}
