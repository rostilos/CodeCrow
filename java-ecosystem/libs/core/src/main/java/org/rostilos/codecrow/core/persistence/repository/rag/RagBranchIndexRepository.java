package org.rostilos.codecrow.core.persistence.repository.rag;

import jakarta.persistence.LockModeType;
import org.rostilos.codecrow.core.model.rag.RagBranchIndex;
import org.rostilos.codecrow.core.model.rag.RagBranchIndexKind;
import org.springframework.data.jpa.repository.JpaRepository;
import org.springframework.data.jpa.repository.Lock;
import org.springframework.data.jpa.repository.Modifying;
import org.springframework.data.jpa.repository.Query;
import org.springframework.data.repository.query.Param;
import org.springframework.stereotype.Repository;

import java.util.List;
import java.util.Optional;

/**
 * Repository for RAG branch index tracking.
 */
@Repository
public interface RagBranchIndexRepository extends JpaRepository<RagBranchIndex, Long> {

    interface OperatorAliasCandidate {
        Long getProjectId();
        String getWorkspaceName();
        String getProjectNamespace();
        String getBranchName();
        String getRevision();
        String getCollectionName();
        RagBranchIndexKind getIndexKind();
    }

    Optional<RagBranchIndex> findByProjectIdAndBranchName(Long projectId, String branchName);

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

    @Query("SELECT CASE WHEN COUNT(b) > 0 THEN true ELSE false END FROM RagBranchIndex b " +
           "WHERE b.project.id = :projectId AND b.branchName = :branchName")
    boolean existsByProjectIdAndBranchName(@Param("projectId") Long projectId, @Param("branchName") String branchName);

    @Modifying
    void deleteByProjectId(Long projectId);

    @Modifying
    void deleteByProjectIdAndBranchName(Long projectId, String branchName);

    @Query("SELECT b.branchName FROM RagBranchIndex b WHERE b.project.id = :projectId")
    List<String> findBranchNamesByProjectId(@Param("projectId") Long projectId);

    /**
     * Reads the immutable values needed by optional operator-alias repair.
     * Returning a scalar projection lets the database transaction finish
     * before the caller performs any potentially slow RAG/Qdrant request.
     */
    @Query("""
            SELECT b.project.id AS projectId,
                   b.project.workspace.name AS workspaceName,
                   b.project.namespace AS projectNamespace,
                   b.branchName AS branchName,
                   b.activeGeneration.revision AS revision,
                   b.activeGeneration.collectionName AS collectionName,
                   b.indexKind AS indexKind
            FROM RagBranchIndex b
            WHERE b.activeGeneration IS NOT NULL
              AND b.indexKind IN (
                org.rostilos.codecrow.core.model.rag.RagBranchIndexKind.PRIMARY,
                org.rostilos.codecrow.core.model.rag.RagBranchIndexKind.DURABLE
              )
            """)
    List<OperatorAliasCandidate> findOperatorAliasCandidates();
}
