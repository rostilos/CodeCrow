package org.rostilos.codecrow.core.persistence.repository.rag;

import jakarta.persistence.LockModeType;
import org.rostilos.codecrow.core.model.rag.RagBranchIndex;
import org.rostilos.codecrow.core.model.rag.RagBranchIndexKind;
import org.rostilos.codecrow.core.model.project.config.ProjectConfig;
import org.springframework.data.jpa.repository.JpaRepository;
import org.springframework.data.jpa.repository.Lock;
import org.springframework.data.jpa.repository.Modifying;
import org.springframework.data.jpa.repository.Query;
import org.springframework.data.repository.query.Param;
import org.springframework.stereotype.Repository;
import org.springframework.transaction.annotation.Transactional;

import java.time.OffsetDateTime;
import java.util.Collection;
import java.util.List;
import java.util.Optional;

/**
 * Repository for RAG branch index tracking.
 */
@Repository
public interface RagBranchIndexRepository extends JpaRepository<RagBranchIndex, Long> {

    /**
     * Immutable coordinates required to advance the currently published
     * generation. Keeping this boundary scalar prevents callers from carrying
     * a lazy generation proxy into long-running VCS or RAG operations after the
     * repository transaction has closed.
     */
    interface ActiveGenerationCoordinates {
        Long getGenerationId();
        String getRevision();
        String getCollectionName();
        String getManifestDigest();
        String getRepresentationFingerprint();
        Integer getFileCount();
        Integer getChunkCount();
        OffsetDateTime getActivatedAt();
    }

    interface ReconciliationCoordinates {
        Long getProjectId();
        String getBranchName();
        String getDesiredCommitHash();
        String getActiveRevision();
        String getActiveRepresentationFingerprint();
        String getErrorMessage();
        OffsetDateTime getLastFailedAt();
    }

    interface TransientCleanupCandidate {
        Long getBranchIndexId();
        Long getProjectId();
        String getWorkspaceName();
        String getProjectNamespace();
        String getBranchName();
        OffsetDateTime getLastAccessedAt();
        OffsetDateTime getUpdatedAt();
        String getCleanupClaimToken();
        OffsetDateTime getCleanupClaimedAt();
        ProjectConfig getProjectConfiguration();
    }

    Optional<RagBranchIndex> findByProjectIdAndBranchName(Long projectId, String branchName);

    @Query("""
            SELECT g.id AS generationId,
                   g.revision AS revision,
                   g.collectionName AS collectionName,
                   g.manifestDigest AS manifestDigest,
                   g.representationFingerprint AS representationFingerprint,
                   g.fileCount AS fileCount,
                   g.chunkCount AS chunkCount,
                   g.activatedAt AS activatedAt
            FROM RagBranchIndex b
            JOIN b.activeGeneration g
            WHERE b.project.id = :projectId
              AND b.branchName = :branchName
              AND b.cleanupClaimToken IS NULL
            """)
    Optional<ActiveGenerationCoordinates> findActiveGenerationCoordinates(
            @Param("projectId") Long projectId,
            @Param("branchName") String branchName);

    @Query("""
            SELECT b.project.id AS projectId,
                   b.branchName AS branchName,
                   b.desiredCommitHash AS desiredCommitHash,
                   g.revision AS activeRevision,
                   g.representationFingerprint AS activeRepresentationFingerprint,
                   b.errorMessage AS errorMessage,
                   b.lastFailedAt AS lastFailedAt
            FROM RagBranchIndex b
            LEFT JOIN b.activeGeneration g
            WHERE b.project.id = :projectId
              AND b.branchName = :branchName
            """)
    Optional<ReconciliationCoordinates> findReconciliationCoordinates(
            @Param("projectId") Long projectId,
            @Param("branchName") String branchName);

    /** One bounded scheduler query for every already-observed branch in a project page. */
    @Query("""
            SELECT b.project.id AS projectId,
                   b.branchName AS branchName,
                   b.desiredCommitHash AS desiredCommitHash,
                   g.revision AS activeRevision,
                   g.representationFingerprint AS activeRepresentationFingerprint,
                   b.errorMessage AS errorMessage,
                   b.lastFailedAt AS lastFailedAt
            FROM RagBranchIndex b
            LEFT JOIN b.activeGeneration g
            WHERE b.project.id IN :projectIds
              AND b.cleanupClaimToken IS NULL
            """)
    List<ReconciliationCoordinates> findReconciliationCoordinatesByProjectIds(
            @Param("projectIds") Collection<Long> projectIds);

    @Lock(LockModeType.PESSIMISTIC_WRITE)
    @Query("SELECT b FROM RagBranchIndex b WHERE b.project.id = :projectId AND b.branchName = :branchName")
    Optional<RagBranchIndex> findByProjectIdAndBranchNameForUpdate(
            @Param("projectId") Long projectId,
            @Param("branchName") String branchName);

    @Lock(LockModeType.PESSIMISTIC_WRITE)
    @Query("SELECT b FROM RagBranchIndex b WHERE b.id = :id")
    Optional<RagBranchIndex> findByIdForPublication(@Param("id") Long id);

    List<RagBranchIndex> findByProjectId(Long projectId);

    List<RagBranchIndex> findByIndexKind(RagBranchIndexKind indexKind);

    /** Scalar scheduler inputs, materialized before any remote cleanup call. */
    @Query("""
            SELECT b.id AS branchIndexId,
                   b.project.id AS projectId,
                   b.project.workspace.name AS workspaceName,
                   b.project.namespace AS projectNamespace,
                   b.branchName AS branchName,
                   b.lastAccessedAt AS lastAccessedAt,
                   b.updatedAt AS updatedAt,
                   b.cleanupClaimToken AS cleanupClaimToken,
                   b.cleanupClaimedAt AS cleanupClaimedAt,
                   b.project.configuration AS projectConfiguration
            FROM RagBranchIndex b
            WHERE b.indexKind <> org.rostilos.codecrow.core.model.rag.RagBranchIndexKind.PRIMARY
            """)
    List<TransientCleanupCandidate> findTransientCleanupCandidates();

    @Modifying(clearAutomatically = true, flushAutomatically = true)
    @Transactional
    @Query("UPDATE RagBranchIndex b SET b.cleanupClaimToken = :claimToken, "
            + "b.cleanupClaimedAt = :claimedAt "
            + "WHERE b.id = :branchIndexId "
            + "AND b.indexKind <> org.rostilos.codecrow.core.model.rag.RagBranchIndexKind.PRIMARY "
            + "AND b.lifecycleStatus <> "
            + "org.rostilos.codecrow.core.model.rag.RagBranchIndexLifecycleStatus.BUILDING "
            + "AND (b.cleanupClaimToken IS NULL OR b.cleanupClaimToken = :claimToken "
            + "OR b.cleanupClaimedAt < :staleBefore) "
            + "AND ((b.lastAccessedAt IS NOT NULL AND b.lastAccessedAt < :cutoff) "
            + "OR (b.lastAccessedAt IS NULL AND b.updatedAt < :cutoff))")
    int claimExpiredTransientForDeletion(
            @Param("branchIndexId") Long branchIndexId,
            @Param("cutoff") OffsetDateTime cutoff,
            @Param("staleBefore") OffsetDateTime staleBefore,
            @Param("claimToken") String claimToken,
            @Param("claimedAt") OffsetDateTime claimedAt);

    @Modifying(clearAutomatically = true, flushAutomatically = true)
    @Transactional
    @Query("UPDATE RagBranchIndex b SET b.cleanupClaimedAt = :claimedAt "
            + "WHERE b.id = :branchIndexId AND b.cleanupClaimToken = :claimToken")
    int heartbeatTransientDeletionClaim(
            @Param("branchIndexId") Long branchIndexId,
            @Param("claimToken") String claimToken,
            @Param("claimedAt") OffsetDateTime claimedAt);

    /**
     * Atomically records exact-generation use unless cleanup already owns the
     * branch. A successful touch makes any expiry claim fail its cutoff check.
     */
    @Modifying(clearAutomatically = true, flushAutomatically = true)
    @Transactional
    @Query("UPDATE RagBranchIndex b SET b.lastAccessedAt = :accessedAt "
            + "WHERE b.project.id = :projectId AND b.branchName = :branchName "
            + "AND b.cleanupClaimToken IS NULL")
    int markAccessedIfUnclaimed(
            @Param("projectId") Long projectId,
            @Param("branchName") String branchName,
            @Param("accessedAt") OffsetDateTime accessedAt);

    @Modifying(clearAutomatically = true, flushAutomatically = true)
    @Transactional
    @Query("UPDATE RagBranchIndex b SET b.cleanupClaimToken = NULL, b.cleanupClaimedAt = NULL "
            + "WHERE b.id = :branchIndexId AND b.cleanupClaimToken = :claimToken")
    int cancelTransientDeletion(
            @Param("branchIndexId") Long branchIndexId,
            @Param("claimToken") String claimToken);

    @Modifying(clearAutomatically = true, flushAutomatically = true)
    @Transactional
    @Query("DELETE FROM RagBranchIndex b WHERE b.id = :branchIndexId "
            + "AND b.indexKind <> org.rostilos.codecrow.core.model.rag.RagBranchIndexKind.PRIMARY "
            + "AND b.cleanupClaimToken = :claimToken")
    int deleteClaimedTransientById(
            @Param("branchIndexId") Long branchIndexId,
            @Param("claimToken") String claimToken);

    @Query("SELECT CASE WHEN COUNT(b) > 0 THEN true ELSE false END FROM RagBranchIndex b " +
           "WHERE b.project.id = :projectId AND b.branchName = :branchName")
    boolean existsByProjectIdAndBranchName(@Param("projectId") Long projectId, @Param("branchName") String branchName);

    @Modifying
    void deleteByProjectId(Long projectId);

    @Modifying
    void deleteByProjectIdAndBranchName(Long projectId, String branchName);

    @Query("SELECT b.branchName FROM RagBranchIndex b WHERE b.project.id = :projectId")
    List<String> findBranchNamesByProjectId(@Param("projectId") Long projectId);

    @Query("SELECT b.branchName FROM RagBranchIndex b "
            + "WHERE b.project.id = :projectId AND b.activeGeneration IS NOT NULL "
            + "ORDER BY b.branchName")
    List<String> findActiveBranchNamesByProjectId(@Param("projectId") Long projectId);

    @Query("SELECT b.branchName FROM RagBranchIndex b "
            + "WHERE b.project.id = :projectId AND b.activeGeneration IS NOT NULL "
            + "AND b.indexKind = org.rostilos.codecrow.core.model.rag.RagBranchIndexKind.PRIMARY")
    Optional<String> findPrimaryActiveBranchNameByProjectId(
            @Param("projectId") Long projectId);

}
