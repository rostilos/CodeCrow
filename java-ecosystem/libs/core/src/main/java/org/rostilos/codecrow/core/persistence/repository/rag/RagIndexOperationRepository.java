package org.rostilos.codecrow.core.persistence.repository.rag;

import org.rostilos.codecrow.core.model.rag.RagIndexOperation;
import org.rostilos.codecrow.core.model.rag.RagIndexOperationStatus;
import org.springframework.data.jpa.repository.JpaRepository;
import org.springframework.data.jpa.repository.Query;
import org.springframework.stereotype.Repository;

import java.time.OffsetDateTime;
import java.util.List;
import java.util.Optional;

@Repository
public interface RagIndexOperationRepository extends JpaRepository<RagIndexOperation, Long> {

    Optional<RagIndexOperation> findByProjectIdAndOperationKey(Long projectId, String operationKey);

    List<RagIndexOperation> findByStatusInAndUpdatedAtBefore(
            List<RagIndexOperationStatus> statuses,
            OffsetDateTime updatedBefore);

    boolean existsByProjectIdAndBranchNameAndStatusIn(
            Long projectId,
            String branchName,
            List<RagIndexOperationStatus> statuses);

    /**
     * Finds already-failed operations whose durable projections still say the
     * producer is active. This repairs drift created by older recovery code or
     * by a partial recovery failure on the next scan.
     */
    @Query(value = """
            SELECT o.*
            FROM rag_index_operation o
            WHERE o.status = 'FAILED'
              AND (
                EXISTS (
                  SELECT 1 FROM job j
                  WHERE j.id = o.job_id
                    AND j.status IN ('PENDING', 'QUEUED', 'RUNNING', 'WAITING')
                )
                OR EXISTS (
                  SELECT 1 FROM rag_index_status s
                  WHERE s.project_id = o.project_id
                    AND s.indexed_branch = o.branch_name
                    AND s.status IN ('INDEXING', 'UPDATING')
                )
                OR EXISTS (
                  SELECT 1 FROM analysis_lock l
                  WHERE l.project_id = o.project_id
                    AND l.branch_name = o.branch_name
                    AND l.analysis_type = 'RAG_INDEXING'
                    AND l.commit_hash IS NOT DISTINCT FROM o.to_revision
                )
              )
            ORDER BY o.completed_at DESC, o.id DESC
            """, nativeQuery = true)
    List<RagIndexOperation> findFailedOperationsWithActiveProjections();
}
