package org.rostilos.codecrow.commitgraph.persistence;

import org.rostilos.codecrow.commitgraph.model.AnalyzedCommit;
import org.springframework.data.jpa.repository.JpaRepository;
import org.springframework.data.jpa.repository.Modifying;
import org.springframework.data.jpa.repository.Query;
import org.springframework.data.repository.query.Param;
import org.springframework.stereotype.Repository;

import java.util.List;
import java.util.Set;

@Repository
public interface AnalyzedCommitRepository extends JpaRepository<AnalyzedCommit, Long> {

    /**
     * Check if a specific commit has been analyzed for a project.
     */
    boolean existsByProjectIdAndCommitHash(Long projectId, String commitHash);

    /**
     * Find all analyzed commit hashes for a project from a given set of hashes.
     * Used for set subtraction: {@code push_commits - already_analyzed = to_analyze}.
     */
    @Query("SELECT ac.commitHash FROM AnalyzedCommit ac WHERE ac.project.id = :projectId AND ac.commitHash IN :hashes")
    Set<String> findAnalyzedHashesByProjectIdAndCommitHashIn(
            @Param("projectId") Long projectId,
            @Param("hashes") List<String> hashes);

    /**
     * Find all analyzed commits for a project.
     */
    List<AnalyzedCommit> findByProjectId(Long projectId);

    // ── Bulk delete (project cleanup) ───────────────────────────────────

    @Modifying
    @Query("DELETE FROM AnalyzedCommit ac WHERE ac.project.id = :projectId")
    void deleteByProjectId(@Param("projectId") Long projectId);
}
