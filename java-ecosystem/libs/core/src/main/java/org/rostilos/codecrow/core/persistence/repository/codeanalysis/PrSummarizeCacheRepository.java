package org.rostilos.codecrow.core.persistence.repository.codeanalysis;

import org.rostilos.codecrow.core.model.codeanalysis.PrSummarizeCache;
import org.springframework.data.jpa.repository.JpaRepository;
import org.springframework.data.jpa.repository.Modifying;
import org.springframework.data.jpa.repository.Query;
import org.springframework.data.repository.query.Param;
import org.springframework.stereotype.Repository;

import java.time.OffsetDateTime;
import java.util.Optional;

@Repository
public interface PrSummarizeCacheRepository extends JpaRepository<PrSummarizeCache, Long> {

    @Query("SELECT s FROM PrSummarizeCache s WHERE s.project.id = :projectId " +
           "AND s.commitHash = :commitHash AND s.prNumber = :prNumber")
    Optional<PrSummarizeCache> findByProjectIdAndCommitHashAndPrNumber(
            @Param("projectId") Long projectId,
            @Param("commitHash") String commitHash,
            @Param("prNumber") Long prNumber
    );

    @Query("SELECT s FROM PrSummarizeCache s WHERE s.project.id = :projectId " +
           "AND s.commitHash = :commitHash AND s.prNumber = :prNumber " +
           "AND (s.expiresAt IS NULL OR s.expiresAt > :now)")
    Optional<PrSummarizeCache> findValidCache(
            @Param("projectId") Long projectId,
            @Param("commitHash") String commitHash,
            @Param("prNumber") Long prNumber,
            @Param("now") OffsetDateTime now
    );

    @Modifying
    @Query("DELETE FROM PrSummarizeCache s WHERE s.expiresAt IS NOT NULL AND s.expiresAt < :now")
    int deleteExpiredEntries(@Param("now") OffsetDateTime now);

    @Modifying
    @Query("DELETE FROM PrSummarizeCache s WHERE s.createdAt < :cutoffTime")
    int deleteOldEntries(@Param("cutoffTime") OffsetDateTime cutoffTime);

    @Query("SELECT COUNT(s) > 0 FROM PrSummarizeCache s WHERE s.project.id = :projectId " +
           "AND s.commitHash = :commitHash AND s.prNumber = :prNumber " +
           "AND (s.expiresAt IS NULL OR s.expiresAt > :now)")
    boolean existsValidCache(
            @Param("projectId") Long projectId,
            @Param("commitHash") String commitHash,
            @Param("prNumber") Long prNumber,
            @Param("now") OffsetDateTime now
    );

    @Modifying
    @Query("DELETE FROM PrSummarizeCache s WHERE s.project.id = :projectId")
    void deleteByProjectId(@Param("projectId") Long projectId);
}
