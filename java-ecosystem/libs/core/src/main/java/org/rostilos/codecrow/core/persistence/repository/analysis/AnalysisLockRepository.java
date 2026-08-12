package org.rostilos.codecrow.core.persistence.repository.analysis;

import org.rostilos.codecrow.core.model.analysis.AnalysisLock;
import org.rostilos.codecrow.core.model.analysis.AnalysisLockType;
import org.springframework.data.jpa.repository.JpaRepository;
import org.springframework.data.jpa.repository.Modifying;
import org.springframework.data.jpa.repository.Query;
import org.springframework.data.repository.query.Param;
import org.springframework.stereotype.Repository;

import java.time.OffsetDateTime;
import java.util.List;
import java.util.Optional;

@Repository
public interface AnalysisLockRepository extends JpaRepository<AnalysisLock, Long> {

    Optional<AnalysisLock> findByLockKey(String lockKey);

    Optional<AnalysisLock> findByProjectIdAndBranchNameAndAnalysisType(
            Long projectId,
            String branchName,
            AnalysisLockType analysisType
    );

    List<AnalysisLock> findByProjectIdAndBranchName(Long projectId, String branchName);

    @Query("SELECT l FROM AnalysisLock l WHERE l.expiresAt < :now")
    List<AnalysisLock> findExpiredLocks(@Param("now") OffsetDateTime now);

    @Modifying
    @Query("DELETE FROM AnalysisLock l WHERE l.expiresAt < :now")
    int deleteExpiredLocks(@Param("now") OffsetDateTime now);

    @Modifying
    @Query("DELETE FROM AnalysisLock l WHERE l.lockKey = :lockKey")
    int deleteByLockKey(@Param("lockKey") String lockKey);

    @Query("SELECT CASE WHEN COUNT(l) > 0 THEN true ELSE false END FROM AnalysisLock l " +
           "WHERE l.project.id = :projectId AND l.branchName = :branchName " +
           "AND l.analysisType = :analysisType AND l.expiresAt > :now")
    boolean existsActiveLock(@Param("projectId") Long projectId,
                             @Param("branchName") String branchName,
                             @Param("analysisType") AnalysisLockType analysisType,
                             @Param("now") OffsetDateTime now);

    void deleteByProjectId(Long projectId);
}
