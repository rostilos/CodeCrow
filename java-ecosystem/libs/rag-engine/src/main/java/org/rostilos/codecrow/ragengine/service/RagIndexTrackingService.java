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
            status.setActiveJobId(activeJobId);
        }

        status = ragIndexStatusRepository.save(status);
        log.info("Marked RAG indexing as STARTED for project {} (branch: {})", project.getName(), branchName);
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
            status.setActiveJobId(null);
        }

        status = ragIndexStatusRepository.save(status);
        log.warn("Marked RAG indexing as FAILED for project {}: {}", project.getName(), errorMessage);
        return status;
    }

    /**
     * Refresh the observable activity timestamp for a live repository-index
     * build without changing its terminal state or published index metadata.
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
            Integer chunkCount,
            OffsetDateTime activatedAt) {
        RagIndexStatus status = ragIndexStatusRepository
                .findByProjectIdForUpdate(project.getId())
                .orElseGet(() -> {
                    RagIndexStatus created = new RagIndexStatus();
                    created.setProject(project);
                    created.setWorkspaceName(project.getWorkspace().getName());
                    created.setProjectName(project.getName());
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
        if (activatedAt != null) {
            status.setLastIndexedAt(activatedAt);
        } else if (status.getLastIndexedAt() == null) {
            // Preserve an existing completed checkpoint; only initialize a
            // missing activation timestamp.
            status.setLastIndexedAt(OffsetDateTime.now());
        }
        status.setErrorMessage(null);
        status.setActiveJobId(null);
        ragIndexStatusRepository.save(status);
    }

    @Transactional
    public RagIndexStatus markGenerationRefreshFailed(Project project, String errorMessage) {
        return markGenerationRefreshFailed(project, errorMessage, null);
    }

    @Transactional
    public RagIndexStatus markGenerationRefreshFailed(
            Project project,
            String errorMessage,
            Long expectedActiveJobId) {
        RagIndexStatus status = ragIndexStatusRepository.findByProjectIdForUpdate(project.getId())
                .orElseThrow(
                        () -> new IllegalStateException("RAG index status not found for project: " + project.getId()));

        if (!ownsStatus(status, expectedActiveJobId, "fail repository index refresh")) {
            return status;
        }

        // Restore the usable terminal state and retain the last completed
        // branch/commit checkpoint. The attempted commit is never published.
        status.setStatus(RagIndexingStatus.INDEXED);
        status.setErrorMessage("Repository index refresh failed: " + errorMessage);
        status.setActiveJobId(null);

        status = ragIndexStatusRepository.save(status);
        log.warn("Repository index refresh failed for project {}; retaining the active generation: {}",
                project.getName(), errorMessage);
        return status;
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
