package org.rostilos.codecrow.core.persistence.repository.rag;

import org.rostilos.codecrow.core.model.rag.RagBranchIndexGeneration;
import org.rostilos.codecrow.core.model.rag.RagBranchIndexGenerationStatus;
import org.springframework.data.jpa.repository.JpaRepository;
import org.springframework.data.jpa.repository.Modifying;
import org.springframework.data.jpa.repository.Query;
import org.springframework.data.repository.query.Param;
import org.springframework.stereotype.Repository;
import org.springframework.transaction.annotation.Transactional;

import java.time.OffsetDateTime;
import java.util.List;
import java.util.Optional;

@Repository
public interface RagBranchIndexGenerationRepository extends JpaRepository<RagBranchIndexGeneration, Long> {

    interface CleanupGenerationCandidate {
        Long getGenerationId();
        String getCollectionName();
        String getRevision();
        String getManifestDigest();
        RagBranchIndexGenerationStatus getStatus();
    }

    interface RetirementCandidate extends CleanupGenerationCandidate {
        String getWorkspaceName();
        String getProjectNamespace();
        String getBranchName();
    }

    @Query("""
            SELECT g.id AS generationId, g.collectionName AS collectionName,
                   g.revision AS revision, g.manifestDigest AS manifestDigest,
                   g.status AS status, b.project.workspace.name AS workspaceName,
                   b.project.namespace AS projectNamespace, b.branchName AS branchName
            FROM RagBranchIndexGeneration g JOIN g.branchIndex b
            WHERE b.cleanupClaimToken IS NULL
              AND (b.activeGeneration IS NULL OR b.activeGeneration.id <> g.id)
              AND g.manifestDigest IS NOT NULL
              AND (g.status = org.rostilos.codecrow.core.model.rag.RagBranchIndexGenerationStatus.RETIRING OR (g.status = org.rostilos.codecrow.core.model.rag.RagBranchIndexGenerationStatus.SUPERSEDED AND g.supersededAt < :cutoff))
            ORDER BY g.supersededAt, g.id
            """)
    List<RetirementCandidate> findRetirementCandidates(@Param("cutoff") OffsetDateTime cutoff);

    @Modifying
    @Transactional
    @Query("""
            UPDATE RagBranchIndexGeneration g SET g.status = org.rostilos.codecrow.core.model.rag.RagBranchIndexGenerationStatus.RETIRING
            WHERE g.id = :id AND g.status = org.rostilos.codecrow.core.model.rag.RagBranchIndexGenerationStatus.SUPERSEDED AND g.supersededAt < :cutoff
              AND EXISTS (
                  SELECT 1 FROM RagBranchIndex b WHERE b.id = g.branchIndex.id
                    AND b.cleanupClaimToken IS NULL
                    AND (b.activeGeneration IS NULL OR b.activeGeneration.id <> g.id)
              )
            """)
    int beginRetirement(@Param("id") Long id, @Param("cutoff") OffsetDateTime cutoff);

    @Modifying
    @Transactional
    @Query("UPDATE RagBranchIndexGeneration g SET g.status = org.rostilos.codecrow.core.model.rag.RagBranchIndexGenerationStatus.RETIRED WHERE g.id = :id AND g.status = org.rostilos.codecrow.core.model.rag.RagBranchIndexGenerationStatus.RETIRING")
    int finishRetirement(@Param("id") Long id);

    boolean existsByCollectionName(String collectionName);

    Optional<RagBranchIndexGeneration> findFirstByBranchIndexIdAndRevisionAndStatusInOrderByCreatedAtDesc(
            Long branchIndexId,
            String revision,
            List<RagBranchIndexGenerationStatus> statuses);

    List<RagBranchIndexGeneration> findByBranchIndexIdOrderByCreatedAtDesc(Long branchIndexId);

    @Query("SELECT g.id AS generationId, g.collectionName AS collectionName, "
            + "g.revision AS revision, g.manifestDigest AS manifestDigest, "
            + "g.status AS status "
            + "FROM RagBranchIndexGeneration g WHERE g.branchIndex.id = :branchIndexId "
            + "AND g.status <> org.rostilos.codecrow.core.model.rag.RagBranchIndexGenerationStatus.RETIRED AND g.manifestDigest IS NOT NULL "
            + "ORDER BY g.createdAt DESC")
    List<CleanupGenerationCandidate> findCleanupCandidatesByBranchIndexId(
            @Param("branchIndexId") Long branchIndexId);

    @Query("SELECT g FROM RagBranchIndexGeneration g "
            + "JOIN g.branchIndex b WHERE b.project.id = :projectId "
            + "AND b.branchName = :branchName AND g.revision = :revision "
            + "AND b.cleanupClaimToken IS NULL "
            + "AND g.status IN :statuses ORDER BY g.createdAt DESC")
    List<RagBranchIndexGeneration> findAvailableExactGeneration(
            @Param("projectId") Long projectId,
            @Param("branchName") String branchName,
            @Param("revision") String revision,
            @Param("statuses") List<RagBranchIndexGenerationStatus> statuses);

}
