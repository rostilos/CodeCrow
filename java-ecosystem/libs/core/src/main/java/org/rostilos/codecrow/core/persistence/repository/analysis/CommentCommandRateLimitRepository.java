package org.rostilos.codecrow.core.persistence.repository.analysis;

import org.rostilos.codecrow.core.model.analysis.CommentCommandRateLimit;
import org.springframework.data.jpa.repository.JpaRepository;
import org.springframework.data.jpa.repository.Modifying;
import org.springframework.data.jpa.repository.Query;
import org.springframework.data.repository.query.Param;
import org.springframework.stereotype.Repository;

import java.time.OffsetDateTime;
import java.util.Optional;

@Repository
public interface CommentCommandRateLimitRepository extends JpaRepository<CommentCommandRateLimit, Long> {

    @Query("SELECT r FROM CommentCommandRateLimit r WHERE r.project.id = :projectId AND r.windowStart = :windowStart ORDER BY r.id ASC LIMIT 1")
    Optional<CommentCommandRateLimit> findByProjectIdAndWindowStart(
            @Param("projectId") Long projectId,
            @Param("windowStart") OffsetDateTime windowStart
    );
    @Query("SELECT r FROM CommentCommandRateLimit r WHERE r.project.id = :projectId ORDER BY r.windowStart DESC LIMIT 1")
    Optional<CommentCommandRateLimit> findLatestByProjectId(@Param("projectId") Long projectId);

    @Modifying
    @Query("DELETE FROM CommentCommandRateLimit r WHERE r.windowStart < :cutoffTime")
    int deleteOldRecords(@Param("cutoffTime") OffsetDateTime cutoffTime);

    @Query("SELECT COALESCE(SUM(r.commandCount), 0) FROM CommentCommandRateLimit r " +
           "WHERE r.project.id = :projectId AND r.windowStart >= :windowStart")
    int countCommandsInWindow(
            @Param("projectId") Long projectId,
            @Param("windowStart") OffsetDateTime windowStart
    );

    /**
     * Atomic upsert: increments command count if record exists, creates with count=1 if not.
     * Uses PostgreSQL ON CONFLICT DO UPDATE to avoid race conditions.
     */
    @Modifying
    @Query(value = """
        INSERT INTO comment_command_rate_limit (project_id, window_start, command_count, last_command_at)
        VALUES (:projectId, :windowStart, 1, NOW())
        ON CONFLICT (project_id, window_start) 
        DO UPDATE SET 
            command_count = comment_command_rate_limit.command_count + 1,
            last_command_at = NOW()
        """, nativeQuery = true)
    void upsertCommandCount(
            @Param("projectId") Long projectId,
            @Param("windowStart") OffsetDateTime windowStart
    );
}
