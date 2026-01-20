package org.rostilos.codecrow.core.persistence.repository.rag;

import org.rostilos.codecrow.core.model.rag.DeltaIndexStatus;
import org.rostilos.codecrow.core.model.rag.RagDeltaIndex;
import org.springframework.data.jpa.repository.JpaRepository;
import org.springframework.data.jpa.repository.Modifying;
import org.springframework.data.jpa.repository.Query;
import org.springframework.data.repository.query.Param;
import org.springframework.stereotype.Repository;

import java.time.OffsetDateTime;
import java.util.List;
import java.util.Optional;

/**
 * Repository for RAG delta index operations.
 */
@Repository
public interface RagDeltaIndexRepository extends JpaRepository<RagDeltaIndex, Long> {

    Optional<RagDeltaIndex> findByProjectIdAndBranchName(Long projectId, String branchName);

    List<RagDeltaIndex> findByProjectId(Long projectId);

    List<RagDeltaIndex> findByProjectIdAndStatus(Long projectId, DeltaIndexStatus status);

    @Query("SELECT d FROM RagDeltaIndex d WHERE d.project.id = :projectId AND d.status = 'READY'")
    List<RagDeltaIndex> findReadyByProjectId(@Param("projectId") Long projectId);

    @Query("SELECT d FROM RagDeltaIndex d WHERE d.lastAccessedAt < :threshold OR d.lastAccessedAt IS NULL")
    List<RagDeltaIndex> findStaleIndexes(@Param("threshold") OffsetDateTime threshold);

    @Query("SELECT d FROM RagDeltaIndex d WHERE d.status = 'ARCHIVED' AND d.updatedAt < :threshold")
    List<RagDeltaIndex> findArchivedIndexesOlderThan(@Param("threshold") OffsetDateTime threshold);

    List<RagDeltaIndex> findByProjectIdAndBaseBranch(Long projectId, String baseBranch);

    @Query("SELECT CASE WHEN COUNT(d) > 0 THEN true ELSE false END FROM RagDeltaIndex d " +
           "WHERE d.project.id = :projectId AND d.branchName = :branchName AND d.status = 'READY'")
    boolean existsReadyDeltaIndex(@Param("projectId") Long projectId, @Param("branchName") String branchName);

    @Modifying
    @Query("UPDATE RagDeltaIndex d SET d.status = 'STALE', d.updatedAt = CURRENT_TIMESTAMP " +
           "WHERE d.project.id = :projectId AND d.baseBranch = :baseBranch AND d.status = 'READY'")
    int markDeltasAsStale(@Param("projectId") Long projectId, @Param("baseBranch") String baseBranch);

    @Modifying
    void deleteByProjectId(Long projectId);

    @Modifying
    void deleteByProjectIdAndBranchName(Long projectId, String branchName);

    long countByProjectId(Long projectId);

    @Query("SELECT COUNT(d) FROM RagDeltaIndex d WHERE d.project.id = :projectId AND d.status = 'READY'")
    long countReadyByProjectId(@Param("projectId") Long projectId);

    @Query("SELECT COALESCE(SUM(d.chunkCount), 0) FROM RagDeltaIndex d " +
           "WHERE d.project.id = :projectId AND d.status = 'READY'")
    long getTotalChunkCountByProjectId(@Param("projectId") Long projectId);
}
