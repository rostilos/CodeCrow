package org.rostilos.codecrow.core.persistence.repository.job;

import org.rostilos.codecrow.core.model.job.JobLog;
import org.rostilos.codecrow.core.model.job.JobLogLevel;
import org.springframework.data.domain.Page;
import org.springframework.data.domain.Pageable;
import org.springframework.data.jpa.repository.JpaRepository;
import org.springframework.data.jpa.repository.Modifying;
import org.springframework.data.jpa.repository.Query;
import org.springframework.data.repository.query.Param;

import java.time.OffsetDateTime;
import java.util.List;
import java.util.Optional;

public interface JobLogRepository extends JpaRepository<JobLog, Long> {

    Optional<JobLog> findByExternalId(String externalId);

    @Query("SELECT l FROM JobLog l WHERE l.job.id = :jobId ORDER BY l.sequenceNumber ASC")
    List<JobLog> findByJobIdOrderBySequence(@Param("jobId") Long jobId);

    @Query("SELECT l FROM JobLog l WHERE l.job.id = :jobId ORDER BY l.sequenceNumber ASC")
    Page<JobLog> findByJobId(@Param("jobId") Long jobId, Pageable pageable);

    @Query("SELECT l FROM JobLog l WHERE l.job.id = :jobId AND l.sequenceNumber > :afterSequence " +
            "ORDER BY l.sequenceNumber ASC")
    List<JobLog> findByJobIdAfterSequence(
            @Param("jobId") Long jobId,
            @Param("afterSequence") Long afterSequence
    );

    @Query("SELECT l FROM JobLog l WHERE l.job.id = :jobId AND l.level = :level " +
            "ORDER BY l.sequenceNumber ASC")
    List<JobLog> findByJobIdAndLevel(
            @Param("jobId") Long jobId,
            @Param("level") JobLogLevel level
    );

    @Query("SELECT l FROM JobLog l WHERE l.job.id = :jobId AND l.step = :step " +
            "ORDER BY l.sequenceNumber ASC")
    List<JobLog> findByJobIdAndStep(
            @Param("jobId") Long jobId,
            @Param("step") String step
    );

    @Query("SELECT COALESCE(MAX(l.sequenceNumber), 0) + 1 FROM JobLog l WHERE l.job.id = :jobId")
    Long getNextSequenceNumber(@Param("jobId") Long jobId);

    @Query("SELECT COALESCE(MAX(l.sequenceNumber), 0) FROM JobLog l WHERE l.job.id = :jobId")
    Long getLatestSequenceNumber(@Param("jobId") Long jobId);

    @Query("SELECT COUNT(l) FROM JobLog l WHERE l.job.id = :jobId")
    long countByJobId(@Param("jobId") Long jobId);

    @Query("SELECT COUNT(l) FROM JobLog l WHERE l.job.id = :jobId AND l.level = 'ERROR'")
    long countErrorsByJobId(@Param("jobId") Long jobId);

    @Modifying
    @Query("DELETE FROM JobLog l WHERE l.job.id = :jobId")
    void deleteByJobId(@Param("jobId") Long jobId);

    @Query("SELECT l FROM JobLog l WHERE l.job.id = :jobId " +
            "AND l.timestamp >= :startTime AND l.timestamp <= :endTime " +
            "ORDER BY l.sequenceNumber ASC")
    List<JobLog> findByJobIdAndTimeRange(
            @Param("jobId") Long jobId,
            @Param("startTime") OffsetDateTime startTime,
            @Param("endTime") OffsetDateTime endTime
    );

    @Query("SELECT l FROM JobLog l WHERE l.job.id = :jobId " +
            "AND LOWER(l.message) LIKE LOWER(CONCAT('%', :searchTerm, '%')) " +
            "ORDER BY l.sequenceNumber ASC")
    List<JobLog> searchByMessage(
            @Param("jobId") Long jobId,
            @Param("searchTerm") String searchTerm
    );

    @Modifying
    @Query("DELETE FROM JobLog l WHERE l.job.project.id = :projectId")
    void deleteByProjectId(@Param("projectId") Long projectId);
}
