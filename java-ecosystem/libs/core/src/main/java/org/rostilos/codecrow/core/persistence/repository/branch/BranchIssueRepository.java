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

/**
 * Repository for {@link BranchIssue} — now a full independent entity.
 * All queries use BranchIssue's own fields (filePath, severity, etc.).
 * No JOIN FETCH to CodeAnalysisIssue needed for data access.
 */
@Repository
public interface BranchIssueRepository extends JpaRepository<BranchIssue, Long> {

    // ── Lookup by origin issue (provenance) ─────────────────────────────

    @Query("SELECT bi FROM BranchIssue bi WHERE bi.branch.id = :branchId AND bi.originIssue.id = :originIssueId")
    Optional<BranchIssue> findByBranchIdAndOriginIssueId(
        @Param("branchId") Long branchId,
        @Param("originIssueId") Long originIssueId);

    /** @deprecated Use {@link #findByBranchIdAndOriginIssueId} or content-fingerprint lookup. */
    @Deprecated
    default Optional<BranchIssue> findByBranchIdAndCodeAnalysisIssueId(Long branchId, Long codeAnalysisIssueId) {
        return findByBranchIdAndOriginIssueId(branchId, codeAnalysisIssueId);
    }

    // ── Content-fingerprint dedup lookup ─────────────────────────────────

    Optional<BranchIssue> findByBranchIdAndContentFingerprint(Long branchId, String contentFingerprint);

    // ── Basic finders ───────────────────────────────────────────────────

    List<BranchIssue> findByBranchId(Long branchId);

    @Query("SELECT bi FROM BranchIssue bi WHERE bi.originIssue.id = :originIssueId")
    List<BranchIssue> findByOriginIssueId(@Param("originIssueId") Long originIssueId);

    /** @deprecated Use {@link #findByOriginIssueId}. */
    @Deprecated
    default List<BranchIssue> findByCodeAnalysisIssueId(Long codeAnalysisIssueId) {
        return findByOriginIssueId(codeAnalysisIssueId);
    }

    // ── Bulk operations ─────────────────────────────────────────────────

    void deleteByBranchId(Long branchId);

    @Modifying
    @Query("DELETE FROM BranchIssue bi WHERE bi.branch.id IN (SELECT b.id FROM Branch b WHERE b.project.id = :projectId)")
    void deleteByProjectId(@Param("projectId") Long projectId);

    // ── Branch-scoped unresolved query ───────────────────────────────────

    @Query("SELECT bi FROM BranchIssue bi " +
           "WHERE bi.branch.id = :branchId " +
           "AND bi.resolved = false")
    List<BranchIssue> findAllUnresolvedByBranchId(@Param("branchId") Long branchId);

    // ── File-scoped queries (use BranchIssue's own filePath) ────────────

    @Query("SELECT bi FROM BranchIssue bi " +
           "WHERE bi.branch.id = :branchId " +
           "AND bi.filePath = :filePath " +
           "AND bi.resolved = false")
    List<BranchIssue> findUnresolvedByBranchIdAndFilePath(
        @Param("branchId") Long branchId,
        @Param("filePath") String filePath
    );

    @Query("SELECT bi FROM BranchIssue bi " +
           "WHERE bi.branch.id = :branchId " +
           "AND bi.filePath = :filePath " +
           "ORDER BY bi.id DESC")
    List<BranchIssue> findByBranchIdAndFilePath(
        @Param("branchId") Long branchId,
        @Param("filePath") String filePath
    );

    @Query("SELECT bi FROM BranchIssue bi " +
           "WHERE bi.branch.id = :branchId " +
           "AND bi.filePath IN :filePaths " +
           "AND bi.resolved = false")
    List<BranchIssue> findUnresolvedByBranchIdAndFilePaths(
        @Param("branchId") Long branchId,
        @Param("filePaths") List<String> filePaths
    );

    // ── Counting queries ────────────────────────────────────────────────

    @Query("SELECT COUNT(bi) FROM BranchIssue bi WHERE bi.branch.id = :branchId AND bi.resolved = false")
    long countUnresolvedByBranchId(@Param("branchId") Long branchId);

    @Query("SELECT COUNT(bi) FROM BranchIssue bi WHERE bi.branch.id = :branchId AND bi.resolved = true")
    long countResolvedByBranchId(@Param("branchId") Long branchId);

    @Query("SELECT COUNT(bi) FROM BranchIssue bi WHERE bi.branch.id = :branchId")
    long countAllByBranchId(@Param("branchId") Long branchId);

    // ── Paged queries (use BranchIssue's own fields for ordering) ───────

    @Query(value = "SELECT bi FROM BranchIssue bi " +
           "WHERE bi.branch.id = :branchId AND bi.resolved = false " +
           "ORDER BY bi.id DESC",
           countQuery = "SELECT COUNT(bi) FROM BranchIssue bi WHERE bi.branch.id = :branchId AND bi.resolved = false")
    Page<BranchIssue> findUnresolvedByBranchIdPaged(
        @Param("branchId") Long branchId,
        Pageable pageable
    );

    @Query(value = "SELECT bi FROM BranchIssue bi " +
           "WHERE bi.branch.id = :branchId AND bi.resolved = true " +
           "ORDER BY bi.id DESC",
           countQuery = "SELECT COUNT(bi) FROM BranchIssue bi WHERE bi.branch.id = :branchId AND bi.resolved = true")
    Page<BranchIssue> findResolvedByBranchIdPaged(
        @Param("branchId") Long branchId,
        Pageable pageable
    );

    @Query(value = "SELECT bi FROM BranchIssue bi " +
           "WHERE bi.branch.id = :branchId " +
           "ORDER BY bi.id DESC",
           countQuery = "SELECT COUNT(bi) FROM BranchIssue bi WHERE bi.branch.id = :branchId")
    Page<BranchIssue> findAllByBranchIdPaged(
        @Param("branchId") Long branchId,
        Pageable pageable
    );

    // ── All branch issues (for in-memory filtering in controller) ───────

    @Query("SELECT bi FROM BranchIssue bi " +
           "WHERE bi.branch.id = :branchId " +
           "ORDER BY bi.id DESC")
    List<BranchIssue> findAllByBranchIdWithIssues(@Param("branchId") Long branchId);
}
