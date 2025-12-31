package org.rostilos.codecrow.core.persistence.repository.branch;

import org.rostilos.codecrow.core.model.branch.BranchIssue;
import org.springframework.data.domain.Page;
import org.springframework.data.domain.Pageable;
import org.springframework.data.jpa.repository.JpaRepository;
import org.springframework.data.jpa.repository.Modifying;
import org.springframework.data.jpa.repository.Query;
import org.springframework.data.repository.query.Param;
import org.springframework.stereotype.Repository;

import java.util.Optional;
import java.util.List;

@Repository
public interface BranchIssueRepository extends JpaRepository<BranchIssue, Long> {

    Optional<BranchIssue> findByBranchIdAndCodeAnalysisIssueId(Long branchId, Long codeAnalysisIssueId);

    List<BranchIssue> findByBranchId(Long branchId);

    @Query("SELECT bi FROM BranchIssue bi WHERE bi.codeAnalysisIssue.id = :codeAnalysisIssueId")
    List<BranchIssue> findByCodeAnalysisIssueId(@Param("codeAnalysisIssueId") Long codeAnalysisIssueId);

    @Modifying(flushAutomatically = true, clearAutomatically = true)
    @Query("UPDATE BranchIssue bi SET bi.resolved = :resolved WHERE bi.codeAnalysisIssue.id = :codeAnalysisIssueId")
    int updateResolvedStatusByCodeAnalysisIssueId(@Param("codeAnalysisIssueId") Long codeAnalysisIssueId, @Param("resolved") boolean resolved);

    void deleteByBranchId(Long branchId);

    @Query("SELECT bi FROM BranchIssue bi " +
           "JOIN FETCH bi.codeAnalysisIssue cai " +
           "LEFT JOIN FETCH cai.analysis a " +
           "LEFT JOIN FETCH a.project " +
           "WHERE bi.branch.id = :branchId " +
           "AND cai.filePath = :filePath " +
           "AND bi.resolved = false")
    List<BranchIssue> findUnresolvedByBranchIdAndFilePath(
        @Param("branchId") Long branchId,
        @Param("filePath") String filePath
    );

    @Query("SELECT bi FROM BranchIssue bi " +
           "JOIN FETCH bi.codeAnalysisIssue cai " +
           "LEFT JOIN FETCH cai.analysis a " +
           "LEFT JOIN FETCH a.project " +
           "WHERE bi.branch.id = :branchId " +
           "AND cai.filePath IN :filePaths " +
           "AND bi.resolved = false")
    List<BranchIssue> findUnresolvedByBranchIdAndFilePaths(
        @Param("branchId") Long branchId,
        @Param("filePaths") List<String> filePaths
    );

    @Query("SELECT COUNT(bi) FROM BranchIssue bi WHERE bi.branch.id = :branchId AND bi.resolved = false")
    long countUnresolvedByBranchId(@Param("branchId") Long branchId);

    @Query("SELECT COUNT(bi) FROM BranchIssue bi WHERE bi.branch.id = :branchId AND bi.resolved = true")
    long countResolvedByBranchId(@Param("branchId") Long branchId);

    @Query("SELECT COUNT(bi) FROM BranchIssue bi WHERE bi.branch.id = :branchId")
    long countAllByBranchId(@Param("branchId") Long branchId);

    @Query(value = "SELECT bi FROM BranchIssue bi " +
           "JOIN FETCH bi.codeAnalysisIssue cai " +
           "WHERE bi.branch.id = :branchId AND bi.resolved = false " +
           "ORDER BY cai.id DESC",
           countQuery = "SELECT COUNT(bi) FROM BranchIssue bi WHERE bi.branch.id = :branchId AND bi.resolved = false")
    Page<BranchIssue> findUnresolvedByBranchIdPaged(
        @Param("branchId") Long branchId,
        Pageable pageable
    );

    @Query(value = "SELECT bi FROM BranchIssue bi " +
           "JOIN FETCH bi.codeAnalysisIssue cai " +
           "WHERE bi.branch.id = :branchId AND bi.resolved = true " +
           "ORDER BY cai.id DESC",
           countQuery = "SELECT COUNT(bi) FROM BranchIssue bi WHERE bi.branch.id = :branchId AND bi.resolved = true")
    Page<BranchIssue> findResolvedByBranchIdPaged(
        @Param("branchId") Long branchId,
        Pageable pageable
    );

    @Query(value = "SELECT bi FROM BranchIssue bi " +
           "JOIN FETCH bi.codeAnalysisIssue cai " +
           "WHERE bi.branch.id = :branchId " +
           "ORDER BY cai.id DESC",
           countQuery = "SELECT COUNT(bi) FROM BranchIssue bi WHERE bi.branch.id = :branchId")
    Page<BranchIssue> findAllByBranchIdPaged(
        @Param("branchId") Long branchId,
        Pageable pageable
    );

    @Modifying
    @Query("DELETE FROM BranchIssue bi WHERE bi.branch.id IN (SELECT b.id FROM Branch b WHERE b.project.id = :projectId)")
    void deleteByProjectId(@Param("projectId") Long projectId);
}
