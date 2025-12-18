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

    @Query("SELECT r FROM CommentCommandRateLimit r WHERE r.project.id = :projectId AND r.windowStart >= :windowStart")
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
}
