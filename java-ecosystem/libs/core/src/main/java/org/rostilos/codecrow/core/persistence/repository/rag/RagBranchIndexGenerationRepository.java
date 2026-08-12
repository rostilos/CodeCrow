package org.rostilos.codecrow.core.persistence.repository.rag;

import org.rostilos.codecrow.core.model.rag.RagBranchIndexGeneration;
import org.rostilos.codecrow.core.model.rag.RagBranchIndexGenerationStatus;
import org.springframework.data.jpa.repository.JpaRepository;
import org.springframework.data.jpa.repository.Query;
import org.springframework.data.repository.query.Param;
import org.springframework.stereotype.Repository;

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

    Optional<RagBranchIndexGeneration> findFirstByBranchIndexIdAndRevisionAndStatusInOrderByCreatedAtDesc(
            Long branchIndexId,
            String revision,
            List<RagBranchIndexGenerationStatus> statuses);

    List<RagBranchIndexGeneration> findByBranchIndexIdOrderByCreatedAtDesc(Long branchIndexId);

    @Query("SELECT g.id AS generationId, g.collectionName AS collectionName, "
            + "g.revision AS revision, g.manifestDigest AS manifestDigest, "
            + "g.status AS status "
            + "FROM RagBranchIndexGeneration g WHERE g.branchIndex.id = :branchIndexId "
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

    /**
     * Physical targets that may still contain a project's PR overlays. Both
     * active and superseded published generations are relevant: a generation
     * can be superseded between review indexing and the close webhook.
     */
    @Query("SELECT DISTINCT g.collectionName FROM RagBranchIndexGeneration g "
            + "JOIN g.branchIndex b WHERE b.project.id = :projectId "
            + "AND g.status IN :statuses")
    List<String> findCollectionNamesByProjectIdAndStatusIn(
            @Param("projectId") Long projectId,
            @Param("statuses") List<RagBranchIndexGenerationStatus> statuses);
}
