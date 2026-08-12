package org.rostilos.codecrow.ragengine.branch;

import org.rostilos.codecrow.core.model.analysis.RagIndexingStatus;
import org.rostilos.codecrow.core.model.project.Project;
import org.rostilos.codecrow.core.model.rag.RagBranchIndex;
import org.rostilos.codecrow.core.persistence.repository.analysis.RagIndexStatusRepository;
import org.rostilos.codecrow.core.persistence.repository.job.JobRepository;
import org.rostilos.codecrow.core.persistence.repository.rag.RagBranchIndexRepository;
import org.rostilos.codecrow.ragengine.service.RagIndexTrackingService;
import org.springframework.stereotype.Service;
import org.springframework.transaction.annotation.Transactional;
import org.springframework.transaction.interceptor.TransactionAspectSupport;

import java.time.OffsetDateTime;
import java.util.HashSet;
import java.util.Objects;
import java.util.Set;

/** Commits one legacy RAG job and all of its Java checkpoints atomically. */
@Service
public class LegacyRagUpdateCompletionService {
    private final JobRepository jobRepository;
    private final RagIndexTrackingService trackingService;
    private final RagIndexStatusRepository indexStatusRepository;
    private final RagBranchIndexRepository branchIndexRepository;

    public LegacyRagUpdateCompletionService(
            JobRepository jobRepository,
            RagIndexTrackingService trackingService,
            RagIndexStatusRepository indexStatusRepository,
            RagBranchIndexRepository branchIndexRepository) {
        this.jobRepository = jobRepository;
        this.trackingService = trackingService;
        this.indexStatusRepository = indexStatusRepository;
        this.branchIndexRepository = branchIndexRepository;
    }

    /**
     * @return {@code false} when recovery or lease expiry won the job row first
     */
    @Transactional
    public boolean complete(
            Project project,
            String branchName,
            String commitHash,
            long jobId,
            OffsetDateTime validAfter,
            boolean tracksProjectStatus,
            int addedFiles,
            int deletedFiles,
            Integer chunkCount,
            Set<String> deletedPaths) {
        OffsetDateTime completedAt = OffsetDateTime.now();
        if (jobRepository.completeOwnedLegacyRagJob(
                jobId, validAfter, completedAt) != 1) {
            return false;
        }

        if (tracksProjectStatus) {
            var status = indexStatusRepository
                    .findByProjectIdForUpdate(project.getId())
                    .orElseThrow(() -> new LegacyRagCompletionConflictException(
                            "RAG index status disappeared before legacy completion"));
            if (status.getStatus() != RagIndexingStatus.UPDATING
                    || !Objects.equals(status.getActiveJobId(), jobId)) {
                throw new LegacyRagCompletionConflictException(
                        "Legacy RAG job no longer owns the project index status");
            }
            trackingService.markUpdatingCompleted(
                    project,
                    branchName,
                    commitHash,
                    addedFiles,
                    deletedFiles,
                    chunkCount,
                    jobId);
        }
        try {
            updateBranchCheckpoint(project, branchName, commitHash, deletedPaths);
        } catch (RuntimeException checkpointFailure) {
            TransactionAspectSupport.currentTransactionStatus().setRollbackOnly();
            throw checkpointFailure;
        }
        return true;
    }

    private void updateBranchCheckpoint(
            Project project,
            String branchName,
            String commitHash,
            Set<String> deletedPaths) {
        RagBranchIndex branchIndex = branchIndexRepository
                .findByProjectIdAndBranchNameForUpdate(
                        project.getId(), branchName)
                .orElseGet(() -> new RagBranchIndex(project, branchName));
        branchIndex.setCommitHash(commitHash);
        branchIndex.setUpdatedAt(OffsetDateTime.now());
        if (deletedPaths != null && !deletedPaths.isEmpty()) {
            Set<String> accumulated = branchIndex.getDeletedFiles() != null
                    ? new HashSet<>(branchIndex.getDeletedFiles())
                    : new HashSet<>();
            accumulated.addAll(deletedPaths);
            branchIndex.setDeletedFiles(accumulated);
        }
        branchIndexRepository.save(branchIndex);
    }

    /** Signals a fenced projection conflict; the producer must not fail newer state. */
    public static final class LegacyRagCompletionConflictException
            extends RuntimeException {
        public LegacyRagCompletionConflictException(String message) {
            super(message);
        }
    }
}
