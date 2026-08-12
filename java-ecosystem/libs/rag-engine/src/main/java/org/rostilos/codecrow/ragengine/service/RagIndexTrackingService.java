package org.rostilos.codecrow.ragengine.service;

import org.rostilos.codecrow.core.model.analysis.RagIndexStatus;
import org.rostilos.codecrow.core.model.analysis.RagIndexingStatus;
import org.rostilos.codecrow.core.model.project.Project;
import org.rostilos.codecrow.core.persistence.repository.analysis.RagIndexStatusRepository;
import org.slf4j.Logger;
import org.slf4j.LoggerFactory;
import org.springframework.stereotype.Service;
import org.springframework.transaction.annotation.Transactional;

import java.time.OffsetDateTime;
import java.util.Objects;
import java.util.Optional;

@Service
public class RagIndexTrackingService {

    private static final Logger log = LoggerFactory.getLogger(RagIndexTrackingService.class);

    private final RagIndexStatusRepository ragIndexStatusRepository;

    public RagIndexTrackingService(RagIndexStatusRepository ragIndexStatusRepository) {
        this.ragIndexStatusRepository = ragIndexStatusRepository;
    }

    @Transactional(readOnly = true)
    public boolean isProjectIndexed(Project project) {
        return ragIndexStatusRepository.isProjectIndexed(project.getId());
    }

    @Transactional(readOnly = true)
    public Optional<RagIndexStatus> getIndexStatus(Project project) {
        return ragIndexStatusRepository.findByProjectId(project.getId());
    }

    @Transactional
    public RagIndexStatus markIndexingStarted(
            Project project,
            String branchName,
            String commitHash,
            Long activeJobId) {
        Optional<RagIndexStatus> existingOpt =
                ragIndexStatusRepository.findByProjectIdForUpdate(project.getId());

        RagIndexStatus status;
        if (existingOpt.isPresent()) {
            status = existingOpt.get();
            status.setStatus(RagIndexingStatus.INDEXING);
            status.setIndexedBranch(branchName);
            status.setIndexedCommitHash(commitHash);
            status.setErrorMessage(null);
            status.setActiveJobId(activeJobId);
        } else {
            status = new RagIndexStatus();
            status.setProject(project);
            status.setWorkspaceName(project.getWorkspace().getName());
            status.setProjectName(project.getName());
            status.setStatus(RagIndexingStatus.INDEXING);
            status.setIndexedBranch(branchName);
            status.setIndexedCommitHash(commitHash);
            status.setCollectionName(generateCollectionName(project));
            status.setActiveJobId(activeJobId);
        }

        status = ragIndexStatusRepository.save(status);
        log.info("Marked RAG indexing as STARTED for project {} (branch: {})", project.getName(), branchName);
        return status;
    }

    @Transactional
    public RagIndexStatus markIndexingCompleted(Project project, String branchName, String commitHash,
            Integer filesIndexed, Integer chunkCount) {
        return markIndexingCompleted(
                project, branchName, commitHash, filesIndexed, chunkCount, null);
    }

    @Transactional
    public RagIndexStatus markIndexingCompleted(
            Project project,
            String branchName,
            String commitHash,
            Integer filesIndexed,
            Integer chunkCount,
            Long expectedActiveJobId) {
        RagIndexStatus status = ragIndexStatusRepository.findByProjectIdForUpdate(project.getId())
                .orElseThrow(
                        () -> new IllegalStateException("RAG index status not found for project: " + project.getId()));

        if (!ownsStatus(status, expectedActiveJobId, "complete full indexing")) {
            return status;
        }

        status.setStatus(RagIndexingStatus.INDEXED);
        status.setIndexedBranch(branchName);
        status.setIndexedCommitHash(commitHash);
        status.setTotalFilesIndexed(filesIndexed);
        if (chunkCount != null) {
            status.setChunkCount(chunkCount);
        }
        status.setLastIndexedAt(OffsetDateTime.now());
        status.setErrorMessage(null);
        status.setActiveJobId(null);
        // Reset failed incremental count on successful full index
        status.resetFailedIncrementalCount();

        status = ragIndexStatusRepository.save(status);
        log.info("Marked RAG indexing as COMPLETED for project {} ({} files, {} chunks)", project.getName(),
                filesIndexed, chunkCount);
        return status;
    }

    @Transactional
    public RagIndexStatus markIndexingFailed(Project project, String errorMessage) {
        return markIndexingFailed(project, errorMessage, null);
    }

    @Transactional
    public RagIndexStatus markIndexingFailed(
            Project project,
            String errorMessage,
            Long expectedActiveJobId) {
        Optional<RagIndexStatus> existingOpt =
                ragIndexStatusRepository.findByProjectIdForUpdate(project.getId());

        RagIndexStatus status;
        if (existingOpt.isPresent()) {
            status = existingOpt.get();
            if (!ownsStatus(status, expectedActiveJobId, "fail full indexing")) {
                return status;
            }
            status.setStatus(RagIndexingStatus.FAILED);
            status.setErrorMessage(errorMessage);
            status.setActiveJobId(null);
        } else {
            status = new RagIndexStatus();
            status.setProject(project);
            status.setWorkspaceName(project.getWorkspace().getName());
            status.setProjectName(project.getName());
            status.setStatus(RagIndexingStatus.FAILED);
            status.setErrorMessage(errorMessage);
            status.setCollectionName(generateCollectionName(project));
            status.setActiveJobId(null);
        }

        status = ragIndexStatusRepository.save(status);
        log.warn("Marked RAG indexing as FAILED for project {}: {}", project.getName(), errorMessage);
        return status;
    }

    /**
     * Refresh the observable activity timestamp for a live full or incremental
     * index without changing its terminal state or published index metadata.
     *
     * @return {@code true} when a live status was refreshed, otherwise
     *         {@code false} when the record is absent or already terminal
     */
    @Transactional
    public boolean markIndexingHeartbeat(Project project) {
        return markIndexingHeartbeat(project, null);
    }

    @Transactional
    public boolean markIndexingHeartbeat(Project project, Long expectedActiveJobId) {
        Optional<RagIndexStatus> statusOpt =
                ragIndexStatusRepository.findByProjectIdForUpdate(project.getId());
        if (statusOpt.isEmpty()) {
            log.warn("Ignoring RAG heartbeat without index status for project {}", project.getId());
            return false;
        }

        RagIndexStatus status = statusOpt.get();
        if (!ownsStatus(status, expectedActiveJobId, "record indexing heartbeat")) {
            return false;
        }
        if (status.getStatus() != RagIndexingStatus.INDEXING
                && status.getStatus() != RagIndexingStatus.UPDATING) {
            log.debug(
                    "Ignoring RAG heartbeat for terminal project status {} ({})",
                    project.getId(),
                    status.getStatus());
            return false;
        }

        status.setUpdatedAt(OffsetDateTime.now());
        ragIndexStatusRepository.save(status);
        return true;
    }

    @Transactional
    public RagIndexStatus markUpdatingStarted(
            Project project,
            String branchName,
            String commitHash,
            Long activeJobId) {
        RagIndexStatus status = ragIndexStatusRepository.findByProjectIdForUpdate(project.getId())
                .orElseThrow(() -> new IllegalStateException("Cannot update non-indexed project: " + project.getId()));

        status.setStatus(RagIndexingStatus.UPDATING);
        status.setErrorMessage(null);
        status.setActiveJobId(activeJobId);

        status = ragIndexStatusRepository.save(status);
        log.info("Marked RAG indexing as UPDATING for project {} toward branch {} commit {}; "
                        + "completed checkpoint remains {}",
                project.getName(), branchName, commitHash, status.getIndexedCommitHash());
        return status;
    }

    /**
     * Atomically aligns the project checkpoint with an already-published exact
     * generation. This is used by same-revision no-ops and post-publication
     * recovery, neither of which needs a synthetic RUNNING status transition.
     * A newer job owner is never overwritten.
     */
    @Transactional
    public boolean reconcilePublishedGeneration(
            Project project,
            String branchName,
            String commitHash,
            Integer fileCount,
            Integer chunkCount) {
        return reconcilePublishedGeneration(
                project, branchName, commitHash, fileCount, chunkCount, null);
    }

    @Transactional
    public boolean reconcilePublishedGeneration(
            Project project,
            String branchName,
            String commitHash,
            Integer fileCount,
            Integer chunkCount,
            Long expectedActiveJobId) {
        Optional<RagIndexStatus> existing =
                ragIndexStatusRepository.findByProjectIdForUpdate(project.getId());
        RagIndexStatus status;
        if (existing.isPresent()) {
            status = existing.get();
            if (expectedActiveJobId != null
                    && !Objects.equals(status.getActiveJobId(), expectedActiveJobId)) {
                log.info(
                        "Preserving RAG status owned by job {} while job {} reconciles "
                                + "a published generation for project {}",
                        status.getActiveJobId(), expectedActiveJobId, project.getId());
                return false;
            }
            if (expectedActiveJobId == null && status.getActiveJobId() != null) {
                log.info(
                        "Preserving RAG status owned by live job {} while reconciling "
                                + "published generation for project {}",
                        status.getActiveJobId(), project.getId());
                return false;
            }
        } else {
            if (expectedActiveJobId != null) {
                log.info(
                        "Published generation for job {} has no owned RAG status to reconcile "
                                + "for project {}",
                        expectedActiveJobId, project.getId());
                return false;
            }
            status = new RagIndexStatus();
            status.setProject(project);
            status.setWorkspaceName(project.getWorkspace().getName());
            status.setProjectName(project.getName());
            status.setCollectionName(generateCollectionName(project));
        }

        status.setStatus(RagIndexingStatus.INDEXED);
        status.setIndexedBranch(branchName);
        status.setIndexedCommitHash(commitHash);
        if (fileCount != null) {
            status.setTotalFilesIndexed(fileCount);
        }
        if (chunkCount != null) {
            status.setChunkCount(chunkCount);
        }
        status.setLastIndexedAt(OffsetDateTime.now());
        status.setErrorMessage(null);
        status.setActiveJobId(null);
        status.resetFailedIncrementalCount();
        ragIndexStatusRepository.save(status);
        return true;
    }

    /**
     * Restores the active exact generation as the completed checkpoint while
     * the caller owns the branch RAG lock. The caller immediately admits the
     * replacement job in the same transaction, so an older status owner
     * cannot later publish through the job-id ownership checks.
     */
    @Transactional
    public void preparePublishedGenerationForUpdate(
            Project project,
            String branchName,
            String commitHash,
            Integer fileCount,
            Integer chunkCount) {
        RagIndexStatus status = ragIndexStatusRepository
                .findByProjectIdForUpdate(project.getId())
                .orElseGet(() -> {
                    RagIndexStatus created = new RagIndexStatus();
                    created.setProject(project);
                    created.setWorkspaceName(project.getWorkspace().getName());
                    created.setProjectName(project.getName());
                    created.setCollectionName(generateCollectionName(project));
                    return created;
                });
        status.setStatus(RagIndexingStatus.INDEXED);
        status.setIndexedBranch(branchName);
        status.setIndexedCommitHash(commitHash);
        if (fileCount != null) {
            status.setTotalFilesIndexed(fileCount);
        }
        if (chunkCount != null) {
            status.setChunkCount(chunkCount);
        }
        status.setLastIndexedAt(OffsetDateTime.now());
        status.setErrorMessage(null);
        status.setActiveJobId(null);
        status.resetFailedIncrementalCount();
        ragIndexStatusRepository.save(status);
    }

    /**
     * Marks an incremental update as completed.
     * Updates totalFilesIndexed by adding addedFiles count and subtracting
     * deletedFiles count.
     */
    @Transactional
    public RagIndexStatus markUpdatingCompleted(Project project, String branchName, String commitHash,
            Integer addedFilesCount, Integer deletedFilesCount, Integer chunkCount) {
        return markUpdatingCompleted(
                project, branchName, commitHash, addedFilesCount, deletedFilesCount,
                chunkCount, true, null);
    }

    @Transactional
    public RagIndexStatus markUpdatingCompleted(
            Project project,
            String branchName,
            String commitHash,
            Integer addedFilesCount,
            Integer deletedFilesCount,
            Integer chunkCount,
            Long expectedActiveJobId) {
        return markUpdatingCompleted(
                project, branchName, commitHash, addedFilesCount, deletedFilesCount,
                chunkCount, true, expectedActiveJobId);
    }

    /**
     * Completes an update while advancing the project-level checkpoint only
     * for the configured base branch. Non-base branches have their own
     * {@code RagBranchIndex} checkpoint.
     */
    @Transactional
    public RagIndexStatus markUpdatingCompleted(Project project, String branchName, String commitHash,
            Integer addedFilesCount, Integer deletedFilesCount, Integer chunkCount,
            boolean advanceProjectCheckpoint) {
        return markUpdatingCompleted(
                project, branchName, commitHash, addedFilesCount, deletedFilesCount,
                chunkCount, advanceProjectCheckpoint, null);
    }

    @Transactional
    public RagIndexStatus markUpdatingCompleted(
            Project project,
            String branchName,
            String commitHash,
            Integer addedFilesCount,
            Integer deletedFilesCount,
            Integer chunkCount,
            boolean advanceProjectCheckpoint,
            Long expectedActiveJobId) {
        RagIndexStatus status = ragIndexStatusRepository.findByProjectIdForUpdate(project.getId())
                .orElseThrow(
                        () -> new IllegalStateException("RAG index status not found for project: " + project.getId()));

        if (!ownsStatus(status, expectedActiveJobId, "complete incremental indexing")) {
            return status;
        }

        status.setStatus(RagIndexingStatus.INDEXED);
        if (advanceProjectCheckpoint) {
            status.setIndexedBranch(branchName);
            status.setIndexedCommitHash(commitHash);
        }

        if (addedFilesCount != null && deletedFilesCount != null && status.getTotalFilesIndexed() != null) {
            int newTotal = status.getTotalFilesIndexed() + addedFilesCount - deletedFilesCount;
            status.setTotalFilesIndexed(Math.max(0, newTotal)); // ensure no negative count
        }

        if (chunkCount != null) {
            status.setChunkCount(chunkCount);
        }

        status.setLastIndexedAt(OffsetDateTime.now());
        status.setErrorMessage(null);
        status.setActiveJobId(null);
        // Reset failed incremental count on successful update
        status.resetFailedIncrementalCount();

        status = ragIndexStatusRepository.save(status);
        log.info("Marked RAG updating as COMPLETED for project {} (added {}, deleted {}, chunks {}, "
                        + "project checkpoint advanced={})",
                project.getName(), addedFilesCount, deletedFilesCount, chunkCount,
                advanceProjectCheckpoint);
        return status;
    }

    /**
     * Mark an incremental update as failed and increment the failure counter.
     * This is used to track repeated failures and suggest full reindex.
     */
    @Transactional
    public RagIndexStatus markIncrementalUpdateFailed(Project project, String errorMessage) {
        return markIncrementalUpdateFailed(project, errorMessage, null);
    }

    @Transactional
    public RagIndexStatus markIncrementalUpdateFailed(
            Project project,
            String errorMessage,
            Long expectedActiveJobId) {
        RagIndexStatus status = ragIndexStatusRepository.findByProjectIdForUpdate(project.getId())
                .orElseThrow(
                        () -> new IllegalStateException("RAG index status not found for project: " + project.getId()));

        if (!ownsStatus(status, expectedActiveJobId, "fail incremental indexing")) {
            return status;
        }

        // Restore the usable terminal state and retain the last completed
        // branch/commit checkpoint. The attempted commit is never published.
        status.setStatus(RagIndexingStatus.INDEXED);
        status.setErrorMessage("Incremental update failed: " + errorMessage);
        status.incrementFailedIncrementalCount();
        status.setActiveJobId(null);

        status = ragIndexStatusRepository.save(status);
        log.warn("Marked RAG incremental update as FAILED for project {} (failure count: {}): {}",
                project.getName(), status.getFailedIncrementalCount(), errorMessage);
        return status;
    }

    /**
     * Quiet, owner-guarded repair used by the periodic legacy-job recovery
     * scan. The scheduler owns degraded/recovered log transitions, preventing
     * the same database outage from producing one warning per scan and job.
     */
    @Transactional
    public boolean recoverAbandonedIncrementalUpdate(
            Long projectId,
            Long expectedActiveJobId,
            String errorMessage) {
        return ragIndexStatusRepository.recoverAbandonedIncrementalUpdate(
                projectId, expectedActiveJobId, errorMessage) == 1;
    }

    @Transactional(readOnly = true)
    public boolean canStartIndexing(Project project) {
        Optional<RagIndexStatus> statusOpt = ragIndexStatusRepository.findByProjectId(project.getId());

        if (statusOpt.isEmpty()) {
            return true;
        }

        RagIndexStatus status = statusOpt.get();
        return status.getStatus() != RagIndexingStatus.INDEXING &&
                status.getStatus() != RagIndexingStatus.UPDATING;
    }

    private String generateCollectionName(Project project) {
        String workspace = project.getWorkspace().getName().replaceAll("[^a-zA-Z0-9_-]", "_");
        String projectName = project.getName().replaceAll("[^a-zA-Z0-9_-]", "_");
        return String.format("%s_%s", workspace, projectName).toLowerCase();
    }

    private boolean ownsStatus(
            RagIndexStatus status,
            Long expectedActiveJobId,
            String transition) {
        if (Objects.equals(status.getActiveJobId(), expectedActiveJobId)) {
            return true;
        }
        log.info(
                "Ignoring stale RAG status transition '{}' for project {}: "
                        + "expected owner {}, current owner {}",
                transition,
                status.getProject() != null ? status.getProject().getId() : null,
                expectedActiveJobId,
                status.getActiveJobId());
        return false;
    }
}
