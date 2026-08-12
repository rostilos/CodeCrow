package org.rostilos.codecrow.core.persistence.repository.rag;

import jakarta.persistence.LockModeType;
import org.rostilos.codecrow.core.model.rag.RagIndexOperation;
import org.rostilos.codecrow.core.model.rag.RagIndexOperationStatus;
import org.springframework.data.jpa.repository.Lock;
import org.springframework.data.jpa.repository.JpaRepository;
import org.springframework.data.jpa.repository.Query;
import org.springframework.data.repository.query.Param;
import org.springframework.stereotype.Repository;

import java.time.OffsetDateTime;
import java.util.List;
import java.util.Optional;

@Repository
public interface RagIndexOperationRepository extends JpaRepository<RagIndexOperation, Long> {

    /** Detached-safe coordinates for abandoned and failed-operation recovery. */
    interface RecoveryOperationProjection {
        Long getOperationId();
        Long getProjectId();
        String getBranchName();
        String getToRevision();
        Long getJobId();
        String getAnalysisLockKey();
        String getErrorMessage();
    }

    interface SucceededOperationProjection {
        Long getOperationId();
        Long getProjectId();
        String getBranchName();
        String getToRevision();
        Long getJobId();
        String getAnalysisLockKey();
        Integer getFileCount();
        Integer getChunkCount();
        Boolean getActiveGeneration();
    }

    Optional<RagIndexOperation> findByProjectIdAndOperationKey(Long projectId, String operationKey);

    @Lock(LockModeType.PESSIMISTIC_WRITE)
    @Query("SELECT o FROM RagIndexOperation o WHERE o.id = :operationId")
    Optional<RagIndexOperation> findByIdForUpdate(@Param("operationId") Long operationId);

    @Query("SELECT o.id AS operationId, o.project.id AS projectId, "
            + "o.branchName AS branchName, o.toRevision AS toRevision, "
            + "o.jobId AS jobId, o.analysisLockKey AS analysisLockKey, "
            + "o.errorMessage AS errorMessage FROM RagIndexOperation o "
            + "WHERE o.status IN :statuses AND o.updatedAt < :updatedBefore")
    List<RecoveryOperationProjection> findRecoverableOperationProjections(
            @Param("statuses") List<RagIndexOperationStatus> statuses,
            @Param("updatedBefore") OffsetDateTime updatedBefore);

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
            SELECT o.id AS "operationId",
                   o.project_id AS "projectId",
                   o.branch_name AS "branchName",
                   o.to_revision AS "toRevision",
                   o.job_id AS "jobId",
                   o.analysis_lock_key AS "analysisLockKey",
                   o.error_message AS "errorMessage"
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
                    AND s.active_job_id = o.job_id
                )
                OR EXISTS (
                  SELECT 1 FROM analysis_lock l
                  WHERE l.project_id = o.project_id
                    AND l.branch_name = o.branch_name
                    AND l.analysis_type = 'RAG_INDEXING'
                    AND l.lock_key = o.analysis_lock_key
                )
              )
            ORDER BY o.completed_at DESC, o.id DESC
            """, nativeQuery = true)
    List<RecoveryOperationProjection> findFailedOperationsWithActiveProjections();

    /**
     * Scalar recovery coordinates for a generation that was published before
     * its job/status/lock projections were terminalized.
     */
    @Query(value = """
            SELECT o.id AS "operationId",
                   o.project_id AS "projectId",
                   o.branch_name AS "branchName",
                   o.to_revision AS "toRevision",
                   o.job_id AS "jobId",
                   o.analysis_lock_key AS "analysisLockKey",
                   g.file_count AS "fileCount",
                   g.chunk_count AS "chunkCount",
                   (b.active_generation_id = g.id) AS "activeGeneration"
            FROM rag_index_operation o
            JOIN rag_branch_index_generation g ON g.id = o.generation_id
            JOIN rag_branch_index b ON b.id = g.branch_index_id
            WHERE o.status = 'SUCCEEDED'
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
                    AND s.active_job_id = o.job_id
                )
                OR EXISTS (
                  SELECT 1 FROM analysis_lock l
                  WHERE l.project_id = o.project_id
                    AND l.branch_name = o.branch_name
                    AND l.analysis_type = 'RAG_INDEXING'
                    AND l.lock_key = o.analysis_lock_key
                )
              )
            ORDER BY o.completed_at DESC, o.id DESC
            """, nativeQuery = true)
    List<SucceededOperationProjection> findSucceededOperationsWithActiveProjections();
}
