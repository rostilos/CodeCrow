package org.rostilos.codecrow.core.persistence.repository.job;

import jakarta.persistence.LockModeType;
import org.rostilos.codecrow.core.model.job.Job;
import org.rostilos.codecrow.core.model.job.JobStatus;
import org.rostilos.codecrow.core.model.job.JobType;
import org.springframework.data.domain.Page;
import org.springframework.data.domain.Pageable;
import org.springframework.data.jpa.repository.JpaRepository;
import org.springframework.data.jpa.repository.Lock;
import org.springframework.data.jpa.repository.Modifying;
import org.springframework.data.jpa.repository.Query;
import org.springframework.data.repository.query.Param;

import java.time.OffsetDateTime;
import java.util.List;
import java.util.Optional;

public interface JobRepository extends JpaRepository<Job, Long> {

    /**
     * Scalar identity for a legacy incremental RAG job whose producer may
     * have disappeared. Keeping recovery coordinates scalar avoids carrying
     * detached project proxies out of the repository transaction.
     */
    interface LegacyRagJobRecoveryCoordinates {
        Long getJobId();
        Long getProjectId();
        String getBranchName();
        String getCommitHash();
        String getErrorMessage();
    }

    Optional<Job> findByExternalId(String externalId);

    @Lock(LockModeType.PESSIMISTIC_WRITE)
    @Query("SELECT j FROM Job j WHERE j.id = :jobId")
    Optional<Job> findByIdForUpdate(@Param("jobId") Long jobId);

    @Query("SELECT j FROM Job j WHERE j.project.id = :projectId ORDER BY j.createdAt DESC")
    Page<Job> findByProjectId(@Param("projectId") Long projectId, Pageable pageable);

    @Query("SELECT j FROM Job j WHERE j.project.id = :projectId AND j.status <> :excludedStatus ORDER BY j.createdAt DESC")
    Page<Job> findByProjectIdAndStatusNot(
            @Param("projectId") Long projectId,
            @Param("excludedStatus") JobStatus excludedStatus,
            Pageable pageable
    );

    @Query("SELECT j FROM Job j WHERE j.project.id = :projectId AND j.jobType = :jobType AND j.status <> :excludedStatus ORDER BY j.createdAt DESC")
    Page<Job> findByProjectIdAndJobTypeAndStatusNot(
            @Param("projectId") Long projectId,
            @Param("jobType") JobType jobType,
            @Param("excludedStatus") JobStatus excludedStatus,
            Pageable pageable
    );

    @Query("SELECT j FROM Job j WHERE j.project.workspace.id = :workspaceId AND j.status <> :excludedStatus ORDER BY j.createdAt DESC")
    Page<Job> findByWorkspaceIdAndStatusNot(
            @Param("workspaceId") Long workspaceId,
            @Param("excludedStatus") JobStatus excludedStatus,
            Pageable pageable
    );

    @Query("SELECT j FROM Job j WHERE j.project.workspace.id = :workspaceId ORDER BY j.createdAt DESC")
    Page<Job> findByWorkspaceId(@Param("workspaceId") Long workspaceId, Pageable pageable);

    @Query("SELECT j FROM Job j WHERE j.project.id = :projectId AND j.status = :status ORDER BY j.createdAt DESC")
    Page<Job> findByProjectIdAndStatus(
            @Param("projectId") Long projectId,
            @Param("status") JobStatus status,
            Pageable pageable
    );

    @Query("SELECT j FROM Job j WHERE j.project.id = :projectId AND j.jobType = :jobType ORDER BY j.createdAt DESC")
    Page<Job> findByProjectIdAndJobType(
            @Param("projectId") Long projectId,
            @Param("jobType") JobType jobType,
            Pageable pageable
    );

    @Query("SELECT j FROM Job j WHERE j.project.id = :projectId AND j.status = :status AND j.jobType = :jobType ORDER BY j.createdAt DESC")
    Page<Job> findByProjectIdAndStatusAndJobType(
            @Param("projectId") Long projectId,
            @Param("status") JobStatus status,
            @Param("jobType") JobType jobType,
            Pageable pageable
    );

    @Query("SELECT j FROM Job j WHERE j.project.id = :projectId " +
            "AND j.status NOT IN (org.rostilos.codecrow.core.model.job.JobStatus.COMPLETED, " +
            "org.rostilos.codecrow.core.model.job.JobStatus.FAILED, " +
            "org.rostilos.codecrow.core.model.job.JobStatus.CANCELLED) " +
            "ORDER BY j.createdAt DESC")
    List<Job> findActiveJobsByProjectId(@Param("projectId") Long projectId);

    @Query("SELECT j FROM Job j WHERE j.project.id = :projectId " +
            "AND j.branchName = :branchName " +
            "AND j.status = org.rostilos.codecrow.core.model.job.JobStatus.RUNNING")
    List<Job> findRunningJobsForBranch(
            @Param("projectId") Long projectId,
            @Param("branchName") String branchName
    );

    @Query("SELECT j FROM Job j WHERE j.project.id = :projectId " +
            "AND j.prNumber = :prNumber " +
            "AND j.status = org.rostilos.codecrow.core.model.job.JobStatus.RUNNING")
    List<Job> findRunningJobsForPr(
            @Param("projectId") Long projectId,
            @Param("prNumber") Long prNumber
    );

    /**
     * PR jobs are persisted before their async handlers start, so PENDING and
     * RUNNING rows form a race-free completion barrier for branch reconciliation.
     */
    @Query("SELECT CASE WHEN COUNT(j) > 0 THEN true ELSE false END FROM Job j " +
            "WHERE j.project.id = :projectId AND j.branchName = :branchName " +
            "AND j.jobType = org.rostilos.codecrow.core.model.job.JobType.PR_ANALYSIS " +
            "AND j.status IN (org.rostilos.codecrow.core.model.job.JobStatus.PENDING, " +
            "org.rostilos.codecrow.core.model.job.JobStatus.QUEUED, " +
            "org.rostilos.codecrow.core.model.job.JobStatus.RUNNING, " +
            "org.rostilos.codecrow.core.model.job.JobStatus.WAITING)")
    boolean existsActivePrAnalysisJob(
            @Param("projectId") Long projectId,
            @Param("branchName") String branchName
    );

    /**
     * Snapshot variant used by a branch job. PR work accepted after the branch
     * job must not extend its barrier indefinitely.
     */
    @Query("SELECT CASE WHEN COUNT(j) > 0 THEN true ELSE false END FROM Job j " +
            "WHERE j.project.id = :projectId AND j.branchName = :branchName " +
            "AND j.jobType = org.rostilos.codecrow.core.model.job.JobType.PR_ANALYSIS " +
            "AND j.id < :beforeJobId " +
            "AND j.status IN (org.rostilos.codecrow.core.model.job.JobStatus.PENDING, " +
            "org.rostilos.codecrow.core.model.job.JobStatus.QUEUED, " +
            "org.rostilos.codecrow.core.model.job.JobStatus.RUNNING, " +
            "org.rostilos.codecrow.core.model.job.JobStatus.WAITING)")
    boolean existsActivePrAnalysisJobBefore(
            @Param("projectId") Long projectId,
            @Param("branchName") String branchName,
            @Param("beforeJobId") Long beforeJobId
    );

    /**
     * A PR accepted after a target-branch update must wait until that older
     * branch job has reconciled repository state and published its RAG update.
     * Later branch jobs wait for the PR instead, so the persisted job id gives
     * both directions one deadlock-free ordering rule.
     */
    @Query("SELECT CASE WHEN COUNT(j) > 0 THEN true ELSE false END FROM Job j " +
            "WHERE j.project.id = :projectId AND j.branchName = :branchName " +
            "AND j.jobType = org.rostilos.codecrow.core.model.job.JobType.BRANCH_ANALYSIS " +
            "AND j.id < :beforeJobId " +
            "AND j.status IN (org.rostilos.codecrow.core.model.job.JobStatus.PENDING, " +
            "org.rostilos.codecrow.core.model.job.JobStatus.QUEUED, " +
            "org.rostilos.codecrow.core.model.job.JobStatus.RUNNING, " +
            "org.rostilos.codecrow.core.model.job.JobStatus.WAITING)")
    boolean existsActiveBranchAnalysisJobBefore(
            @Param("projectId") Long projectId,
            @Param("branchName") String branchName,
            @Param("beforeJobId") Long beforeJobId
    );

    /**
     * Return only the newest analysis attempt for a PR. An abandoned older
     * attempt must not poison branch reconciliation after a newer attempt has
     * already reached a terminal state.
     */
    Optional<Job> findFirstByProjectIdAndBranchNameAndJobTypeAndPrNumberOrderByIdDesc(
            Long projectId,
            String branchName,
            JobType jobType,
            Long prNumber
    );

    /**
     * Newest PR attempt from the branch job's intake snapshot.
     */
    Optional<Job> findFirstByProjectIdAndBranchNameAndJobTypeAndPrNumberAndIdLessThanOrderByIdDesc(
            Long projectId,
            String branchName,
            JobType jobType,
            Long prNumber,
            Long beforeJobId
    );

    /**
     * A later branch job owns the newest target-branch state. Completed/skipped
     * successors still supersede an older job; failed/cancelled successors do not.
     */
    @Query("SELECT CASE WHEN COUNT(j) > 0 THEN true ELSE false END FROM Job j " +
            "WHERE j.project.id = :projectId AND j.branchName = :branchName " +
            "AND j.jobType = org.rostilos.codecrow.core.model.job.JobType.BRANCH_ANALYSIS " +
            "AND j.id > :currentJobId " +
            "AND j.status NOT IN (org.rostilos.codecrow.core.model.job.JobStatus.FAILED, " +
            "org.rostilos.codecrow.core.model.job.JobStatus.CANCELLED)")
    boolean existsNewerBranchAnalysisJob(
            @Param("projectId") Long projectId,
            @Param("branchName") String branchName,
            @Param("currentJobId") Long currentJobId
    );

    @Query("SELECT j FROM Job j WHERE j.status = org.rostilos.codecrow.core.model.job.JobStatus.RUNNING " +
            "AND j.startedAt < :threshold")
    List<Job> findStuckJobs(@Param("threshold") OffsetDateTime threshold);

    @Query("SELECT j FROM Job j WHERE " +
            "j.triggerSource = org.rostilos.codecrow.core.model.job.JobTriggerSource.WEBHOOK " +
            "AND j.jobType IN (org.rostilos.codecrow.core.model.job.JobType.PR_ANALYSIS, " +
            "org.rostilos.codecrow.core.model.job.JobType.BRANCH_ANALYSIS) " +
            "AND j.status IN (org.rostilos.codecrow.core.model.job.JobStatus.PENDING, " +
            "org.rostilos.codecrow.core.model.job.JobStatus.QUEUED) " +
            "AND j.updatedAt < :threshold ORDER BY j.createdAt ASC")
    List<Job> findRecoverableWebhookJobs(
            @Param("threshold") OffsetDateTime threshold,
            Pageable pageable);

    @Query("SELECT j FROM Job j WHERE " +
            "j.triggerSource = org.rostilos.codecrow.core.model.job.JobTriggerSource.WEBHOOK " +
            "AND j.jobType IN (org.rostilos.codecrow.core.model.job.JobType.PR_ANALYSIS, " +
            "org.rostilos.codecrow.core.model.job.JobType.BRANCH_ANALYSIS) " +
            "AND j.status = org.rostilos.codecrow.core.model.job.JobStatus.RUNNING " +
            "AND j.updatedAt < :threshold ORDER BY j.updatedAt ASC")
    List<Job> findAbandonedRunningWebhookJobs(
            @Param("threshold") OffsetDateTime threshold,
            Pageable pageable);

    @Modifying(clearAutomatically = true, flushAutomatically = true)
    @Query("UPDATE Job j SET j.status = org.rostilos.codecrow.core.model.job.JobStatus.QUEUED, " +
            "j.updatedAt = :claimedAt WHERE j.id = :jobId " +
            "AND j.triggerSource = org.rostilos.codecrow.core.model.job.JobTriggerSource.WEBHOOK " +
            "AND j.jobType IN (org.rostilos.codecrow.core.model.job.JobType.PR_ANALYSIS, " +
            "org.rostilos.codecrow.core.model.job.JobType.BRANCH_ANALYSIS) " +
            "AND j.status IN (org.rostilos.codecrow.core.model.job.JobStatus.PENDING, " +
            "org.rostilos.codecrow.core.model.job.JobStatus.QUEUED) " +
            "AND j.updatedAt < :threshold")
    int claimRecoverableWebhookJob(
            @Param("jobId") Long jobId,
            @Param("threshold") OffsetDateTime threshold,
            @Param("claimedAt") OffsetDateTime claimedAt);

    @Modifying
    @Query("UPDATE Job j SET j.updatedAt = :activityAt WHERE j.id = :jobId")
    int touchJob(@Param("jobId") Long jobId, @Param("activityAt") OffsetDateTime activityAt);

    /**
     * Atomically renew a live legacy incremental RAG producer. Exact-generation
     * jobs are owned by {@code RagIndexOperation} and must never share this
     * recovery path.
     */
    @Modifying(clearAutomatically = true, flushAutomatically = true)
    @Query(value = """
            UPDATE job j
               SET updated_at = :renewedAt
             WHERE j.id = :jobId
               AND j.job_type = 'RAG_INCREMENTAL_INDEX'
               AND j.status = 'RUNNING'
               AND j.updated_at >= :validAfter
               AND NOT EXISTS (
                    SELECT 1
                      FROM rag_index_operation o
                     WHERE o.job_id = j.id
               )
            """, nativeQuery = true)
    int renewLegacyRagJobLease(
            @Param("jobId") Long jobId,
            @Param("validAfter") OffsetDateTime validAfter,
            @Param("renewedAt") OffsetDateTime renewedAt);

    /**
     * Find legacy incremental RAG jobs with no durable producer activity. The
     * guarded update below remains the authority; this projection only bounds
     * and prioritizes recovery work.
     */
    @Query("SELECT j.id AS jobId, j.project.id AS projectId, " +
            "j.branchName AS branchName, j.commitHash AS commitHash, " +
            "j.errorMessage AS errorMessage " +
            "FROM Job j WHERE " +
            "j.jobType = org.rostilos.codecrow.core.model.job.JobType.RAG_INCREMENTAL_INDEX " +
            "AND j.status IN (org.rostilos.codecrow.core.model.job.JobStatus.PENDING, " +
            "org.rostilos.codecrow.core.model.job.JobStatus.QUEUED, " +
            "org.rostilos.codecrow.core.model.job.JobStatus.RUNNING, " +
            "org.rostilos.codecrow.core.model.job.JobStatus.WAITING) " +
            "AND j.updatedAt < :threshold " +
            "AND NOT EXISTS (SELECT o.id FROM RagIndexOperation o WHERE o.jobId = j.id) " +
            "ORDER BY j.updatedAt ASC")
    List<LegacyRagJobRecoveryCoordinates> findAbandonedLegacyRagJobs(
            @Param("threshold") OffsetDateTime threshold,
            Pageable pageable);

    /**
     * Win recovery only if neither a heartbeat nor a terminal transition moved
     * the row after selection. This CAS is the fencing point between a live
     * producer and the recovery scheduler.
     */
    @Modifying(clearAutomatically = true, flushAutomatically = true)
    @Query(value = """
            UPDATE job j
               SET status = 'FAILED',
                   completed_at = :failedAt,
                   error_message = :diagnostic,
                   updated_at = :failedAt
             WHERE j.id = :jobId
               AND j.job_type = 'RAG_INCREMENTAL_INDEX'
               AND j.status IN ('PENDING', 'QUEUED', 'RUNNING', 'WAITING')
               AND j.updated_at < :threshold
               AND NOT EXISTS (
                    SELECT 1
                      FROM rag_index_operation o
                     WHERE o.job_id = j.id
               )
            """, nativeQuery = true)
    int failAbandonedLegacyRagJob(
            @Param("jobId") Long jobId,
            @Param("threshold") OffsetDateTime threshold,
            @Param("failedAt") OffsetDateTime failedAt,
            @Param("diagnostic") String diagnostic);

    /**
     * Atomically win the terminal transition for a still-owned legacy RAG
     * producer. The surrounding transaction commits this row together with
     * project and branch checkpoints, so recovery cannot interleave between
     * ownership proof and publication.
     */
    @Modifying(clearAutomatically = true, flushAutomatically = true)
    @Query(value = """
            UPDATE job j
               SET status = 'COMPLETED',
                   completed_at = :completedAt,
                   progress = 100,
                   updated_at = :completedAt
             WHERE j.id = :jobId
               AND j.job_type = 'RAG_INCREMENTAL_INDEX'
               AND j.status = 'RUNNING'
               AND j.updated_at >= :validAfter
               AND NOT EXISTS (
                    SELECT 1
                      FROM rag_index_operation o
                     WHERE o.job_id = j.id
               )
            """, nativeQuery = true)
    int completeOwnedLegacyRagJob(
            @Param("jobId") Long jobId,
            @Param("validAfter") OffsetDateTime validAfter,
            @Param("completedAt") OffsetDateTime completedAt);

    /** Retry projection repair if the process stopped after failing the job. */
    @Query("SELECT j.id AS jobId, j.project.id AS projectId, " +
            "j.branchName AS branchName, j.commitHash AS commitHash, " +
            "j.errorMessage AS errorMessage " +
            "FROM Job j WHERE " +
            "j.jobType = org.rostilos.codecrow.core.model.job.JobType.RAG_INCREMENTAL_INDEX " +
            "AND j.status = org.rostilos.codecrow.core.model.job.JobStatus.FAILED " +
            "AND NOT EXISTS (SELECT o.id FROM RagIndexOperation o WHERE o.jobId = j.id) " +
            "AND EXISTS (SELECT s.id FROM RagIndexStatus s WHERE " +
            "s.project.id = j.project.id AND s.activeJobId = j.id " +
            "AND s.status IN (org.rostilos.codecrow.core.model.analysis.RagIndexingStatus.INDEXING, " +
            "org.rostilos.codecrow.core.model.analysis.RagIndexingStatus.UPDATING)) " +
            "ORDER BY j.updatedAt ASC")
    List<LegacyRagJobRecoveryCoordinates> findFailedLegacyRagJobsWithActiveStatus(
            Pageable pageable);

    @Modifying(clearAutomatically = true, flushAutomatically = true)
    @Query("UPDATE Job j SET j.status = org.rostilos.codecrow.core.model.job.JobStatus.QUEUED, " +
            "j.updatedAt = :claimedAt WHERE j.id = :jobId " +
            "AND j.triggerSource = org.rostilos.codecrow.core.model.job.JobTriggerSource.WEBHOOK " +
            "AND j.jobType IN (org.rostilos.codecrow.core.model.job.JobType.PR_ANALYSIS, " +
            "org.rostilos.codecrow.core.model.job.JobType.BRANCH_ANALYSIS) " +
            "AND j.status = org.rostilos.codecrow.core.model.job.JobStatus.RUNNING " +
            "AND j.updatedAt < :threshold")
    int claimAbandonedRunningWebhookJob(
            @Param("jobId") Long jobId,
            @Param("threshold") OffsetDateTime threshold,
            @Param("claimedAt") OffsetDateTime claimedAt);

    @Query("SELECT COUNT(j) FROM Job j WHERE j.project.id = :projectId " +
            "AND j.status NOT IN (org.rostilos.codecrow.core.model.job.JobStatus.COMPLETED, " +
            "org.rostilos.codecrow.core.model.job.JobStatus.FAILED, " +
            "org.rostilos.codecrow.core.model.job.JobStatus.CANCELLED)")
    long countActiveJobsByProjectId(@Param("projectId") Long projectId);

    @Query("SELECT j FROM Job j WHERE j.codeAnalysis.id = :analysisId")
    Optional<Job> findByCodeAnalysisId(@Param("analysisId") Long analysisId);

    @Query("SELECT j FROM Job j WHERE j.project.id = :projectId AND j.prNumber = :prNumber " +
            "ORDER BY j.createdAt DESC")
    List<Job> findLatestJobsForPr(
            @Param("projectId") Long projectId,
            @Param("prNumber") Long prNumber,
            Pageable pageable
    );

    @Query("DELETE FROM Job j WHERE j.status IN (org.rostilos.codecrow.core.model.job.JobStatus.COMPLETED, " +
            "org.rostilos.codecrow.core.model.job.JobStatus.FAILED, " +
            "org.rostilos.codecrow.core.model.job.JobStatus.CANCELLED) " +
            "AND j.completedAt < :threshold")
    void deleteOldCompletedJobs(@Param("threshold") OffsetDateTime threshold);

    @Query("SELECT j FROM Job j WHERE j.project.id = :projectId " +
            "AND j.createdAt >= :startDate AND j.createdAt <= :endDate " +
            "ORDER BY j.createdAt DESC")
    Page<Job> findByProjectIdAndDateRange(
            @Param("projectId") Long projectId,
            @Param("startDate") OffsetDateTime startDate,
            @Param("endDate") OffsetDateTime endDate,
            Pageable pageable
    );

    @Modifying
    @Query("DELETE FROM Job j WHERE j.project.id = :projectId")
    void deleteByProjectId(@Param("projectId") Long projectId);

    @Modifying
    @Query("DELETE FROM Job j WHERE j.id = :jobId")
    void deleteJobById(@Param("jobId") Long jobId);
}
