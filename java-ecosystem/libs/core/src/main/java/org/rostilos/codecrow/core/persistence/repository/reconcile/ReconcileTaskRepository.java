package org.rostilos.codecrow.core.persistence.repository.reconcile;

import org.rostilos.codecrow.core.model.reconcile.ReconcileTask;
import org.rostilos.codecrow.core.model.reconcile.ReconcileTaskStatus;
import org.springframework.data.jpa.repository.JpaRepository;
import org.springframework.data.jpa.repository.Modifying;
import org.springframework.data.jpa.repository.Query;
import org.springframework.data.repository.query.Param;

import org.springframework.transaction.annotation.Transactional;

import java.time.OffsetDateTime;
import java.util.List;
import java.util.Optional;

public interface ReconcileTaskRepository extends JpaRepository<ReconcileTask, Long> {

    Optional<ReconcileTask> findByExternalId(String externalId);

    /**
     * Find all tasks with a given status, oldest first.
     */
    List<ReconcileTask> findByStatusOrderByCreatedAtAsc(ReconcileTaskStatus status);

    /**
     * Check whether a PENDING or IN_PROGRESS task already exists for this branch.
     */
    @Query("SELECT t FROM ReconcileTask t WHERE t.projectId = :projectId " +
            "AND t.branchName = :branchName " +
            "AND t.status IN (org.rostilos.codecrow.core.model.reconcile.ReconcileTaskStatus.PENDING, " +
            "                 org.rostilos.codecrow.core.model.reconcile.ReconcileTaskStatus.IN_PROGRESS)")
    List<ReconcileTask> findActiveTasksForBranch(
            @Param("projectId") Long projectId,
            @Param("branchName") String branchName
    );

    /**
     * Clean up old completed/failed tasks.
     */
    @Modifying
    @Transactional
    @Query("DELETE FROM ReconcileTask t WHERE t.status IN (" +
            "org.rostilos.codecrow.core.model.reconcile.ReconcileTaskStatus.COMPLETED, " +
            "org.rostilos.codecrow.core.model.reconcile.ReconcileTaskStatus.FAILED) " +
            "AND t.completedAt < :threshold")
    void deleteOldTasks(@Param("threshold") OffsetDateTime threshold);

    /**
     * Find stuck IN_PROGRESS tasks (for timeout handling).
     */
    @Query("SELECT t FROM ReconcileTask t " +
            "WHERE t.status = org.rostilos.codecrow.core.model.reconcile.ReconcileTaskStatus.IN_PROGRESS " +
            "AND t.startedAt < :threshold")
    List<ReconcileTask> findStuckTasks(@Param("threshold") OffsetDateTime threshold);
}
