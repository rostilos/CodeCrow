package org.rostilos.codecrow.core.persistence.repository.job;

import org.rostilos.codecrow.core.model.job.Job;
import org.rostilos.codecrow.core.model.job.JobStatus;
import org.rostilos.codecrow.core.model.job.JobType;
import org.springframework.data.domain.Page;
import org.springframework.data.domain.Pageable;
import org.springframework.data.jpa.repository.JpaRepository;
import org.springframework.data.jpa.repository.Modifying;
import org.springframework.data.jpa.repository.Query;
import org.springframework.data.repository.query.Param;

import java.time.OffsetDateTime;
import java.util.List;
import java.util.Optional;

public interface JobRepository extends JpaRepository<Job, Long> {

    Optional<Job> findByExternalId(String externalId);

    @Query("SELECT j FROM Job j WHERE j.project.id = :projectId ORDER BY j.createdAt DESC")
    Page<Job> findByProjectId(@Param("projectId") Long projectId, Pageable pageable);

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

    @Query("SELECT j FROM Job j WHERE j.status = org.rostilos.codecrow.core.model.job.JobStatus.RUNNING " +
            "AND j.startedAt < :threshold")
    List<Job> findStuckJobs(@Param("threshold") OffsetDateTime threshold);

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
