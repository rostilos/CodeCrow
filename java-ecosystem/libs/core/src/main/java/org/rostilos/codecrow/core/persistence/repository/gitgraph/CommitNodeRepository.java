package org.rostilos.codecrow.core.persistence.repository.gitgraph;

import org.rostilos.codecrow.core.model.gitgraph.CommitAnalysisStatus;
import org.rostilos.codecrow.core.model.gitgraph.CommitNode;
import org.springframework.data.jpa.repository.JpaRepository;
import org.springframework.data.jpa.repository.Modifying;
import org.springframework.data.jpa.repository.Query;
import org.springframework.data.repository.query.Param;
import org.springframework.stereotype.Repository;

import java.util.List;
import java.util.Optional;

@Repository
public interface CommitNodeRepository extends JpaRepository<CommitNode, Long> {

    Optional<CommitNode> findByProjectIdAndCommitHash(Long projectId, String commitHash);

    @Query("SELECT c FROM CommitNode c WHERE c.project.id = :projectId AND c.commitHash IN :hashes")
    List<CommitNode> findByProjectIdAndCommitHashes(@Param("projectId") Long projectId, @Param("hashes") List<String> hashes);

    List<CommitNode> findByProjectId(Long projectId);

    /**
     * Find all commits for a project with a given analysis status.
     */
    List<CommitNode> findByProjectIdAndAnalysisStatus(Long projectId, CommitAnalysisStatus status);

    /**
     * Find all unanalyzed or failed commits for a project (eligible for analysis).
     */
    @Query("SELECT c FROM CommitNode c WHERE c.project.id = :projectId AND c.analysisStatus IN ('NOT_ANALYZED', 'FAILED')")
    List<CommitNode> findUnanalyzedByProjectId(@Param("projectId") Long projectId);

    /**
     * Bulk-mark a set of commits as ANALYZED, linking them to the given analysis.
     * Used by PR analysis where a CodeAnalysis record exists.
     */
    @Modifying
    @Query("UPDATE CommitNode c SET c.analysisStatus = 'ANALYZED', c.analyzedAt = CURRENT_TIMESTAMP, c.analysis.id = :analysisId " +
           "WHERE c.project.id = :projectId AND c.commitHash IN :hashes")
    int markCommitsAnalyzed(@Param("projectId") Long projectId,
                            @Param("hashes") List<String> hashes,
                            @Param("analysisId") Long analysisId);

    /**
     * Bulk-mark a set of commits as ANALYZED without linking to a specific analysis.
     * Used by branch analysis where no CodeAnalysis record is created.
     */
    @Modifying
    @Query("UPDATE CommitNode c SET c.analysisStatus = 'ANALYZED', c.analyzedAt = CURRENT_TIMESTAMP " +
           "WHERE c.project.id = :projectId AND c.commitHash IN :hashes")
    int markCommitsAnalyzedWithoutAnalysis(@Param("projectId") Long projectId,
                                           @Param("hashes") List<String> hashes);

    /**
     * Bulk-mark a set of commits as FAILED.
     */
    @Modifying
    @Query("UPDATE CommitNode c SET c.analysisStatus = 'FAILED' " +
           "WHERE c.project.id = :projectId AND c.commitHash IN :hashes AND c.analysisStatus = 'NOT_ANALYZED'")
    int markCommitsFailed(@Param("projectId") Long projectId,
                          @Param("hashes") List<String> hashes);

    // ── Bulk delete (project cleanup) ───────────────────────────────────

    /**
     * Delete all edges in git_commit_edge that belong to commits of this project.
     * Must be called before deleteByProjectId because git_commit_edge has FKs to git_commit_node.
     */
    @Modifying
    @Query(value = "DELETE FROM git_commit_edge WHERE child_commit_id IN (SELECT id FROM git_commit_node WHERE project_id = :projectId) " +
           "OR parent_commit_id IN (SELECT id FROM git_commit_node WHERE project_id = :projectId)", nativeQuery = true)
    void deleteEdgesByProjectId(@Param("projectId") Long projectId);

    @Modifying
    @Query("DELETE FROM CommitNode c WHERE c.project.id = :projectId")
    void deleteByProjectId(@Param("projectId") Long projectId);

    // ── Git Graph queries ──────────────────────────────────────────────

    /**
     * Get all commit nodes for a project with the linked CodeAnalysis eagerly fetched.
     * Ordered by commit timestamp descending (newest first).
     */
    @Query("SELECT c FROM CommitNode c LEFT JOIN FETCH c.analysis WHERE c.project.id = :projectId ORDER BY c.commitTimestamp DESC")
    List<CommitNode> findByProjectIdWithAnalysis(@Param("projectId") Long projectId);

    /**
     * Get all parent-child edges as pairs of commit hashes (child_hash, parent_hash).
     * Uses a native query to join through the git_commit_edge table.
     */
    @Query(value = "SELECT cn_child.commit_hash AS child_hash, cn_parent.commit_hash AS parent_hash " +
           "FROM git_commit_edge e " +
           "JOIN git_commit_node cn_child ON cn_child.id = e.child_commit_id " +
           "JOIN git_commit_node cn_parent ON cn_parent.id = e.parent_commit_id " +
           "WHERE cn_child.project_id = :projectId", nativeQuery = true)
    List<Object[]> findEdgeHashesByProjectId(@Param("projectId") Long projectId);
}
