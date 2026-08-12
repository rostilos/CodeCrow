package org.rostilos.codecrow.ragengine.branch;

import org.rostilos.codecrow.core.model.rag.RagBranchIndexGenerationStatus;
import org.rostilos.codecrow.core.persistence.repository.rag.RagBranchIndexGenerationRepository;
import org.rostilos.codecrow.core.persistence.repository.rag.RagBranchIndexRepository;
import org.rostilos.codecrow.ragengine.client.RagPipelineClient;
import org.slf4j.Logger;
import org.slf4j.LoggerFactory;
import org.springframework.scheduling.annotation.Scheduled;
import org.springframework.stereotype.Service;

import java.time.OffsetDateTime;
import java.util.Comparator;
import java.util.List;
import java.util.UUID;

/** Removes only expired PR-target generations explicitly classified transient. */
@Service
public class RagTransientBranchIndexCleanupService {
    private static final Logger log = LoggerFactory.getLogger(
            RagTransientBranchIndexCleanupService.class);

    private final RagBranchIndexRepository branchRepository;
    private final RagBranchIndexGenerationRepository generationRepository;
    private final RagPipelineClient pipelineClient;
    private final String cleanupOwner = UUID.randomUUID().toString();
    private CleanupState cleanupState = CleanupState.HEALTHY;

    public RagTransientBranchIndexCleanupService(
            RagBranchIndexRepository branchRepository,
            RagBranchIndexGenerationRepository generationRepository,
            RagPipelineClient pipelineClient) {
        this.branchRepository = branchRepository;
        this.generationRepository = generationRepository;
        this.pipelineClient = pipelineClient;
    }

    @Scheduled(
            fixedDelayString = "${codecrow.rag.transient.cleanup-interval-ms:3600000}",
            initialDelayString = "${codecrow.rag.transient.cleanup-initial-delay-ms:300000}")
    public synchronized void cleanupExpired() {
        OffsetDateTime now = OffsetDateTime.now();
        List<RagBranchIndexRepository.TransientCleanupCandidate> candidates;
        try {
            candidates = branchRepository.findTransientCleanupCandidates();
        } catch (RuntimeException repositoryFailure) {
            recordDegradedRun("Could not read transient RAG cleanup candidates; retrying next run: "
                    + repositoryFailure.getMessage());
            return;
        }

        int rejectedCandidates = 0;
        for (var index : candidates) {
            var config = index.getProjectConfiguration() != null
                    ? index.getProjectConfiguration().ragConfig()
                    : null;
            int retentionDays = config != null
                    ? config.getEffectiveBranchRetentionDays()
                    : 90;
            OffsetDateTime lastUse = index.getLastAccessedAt() != null
                    ? index.getLastAccessedAt()
                    : index.getUpdatedAt();
            if (lastUse == null || !lastUse.isBefore(now.minusDays(retentionDays))) {
                continue;
            }

            OffsetDateTime cutoff = now.minusDays(retentionDays);
            int claimed;
            try {
                claimed = branchRepository.claimExpiredTransientForDeletion(
                        index.getBranchIndexId(), cutoff, now.minusMinutes(10),
                        cleanupOwner, now);
            } catch (RuntimeException repositoryFailure) {
                recordDegradedRun("Transient RAG cleanup could not claim project="
                        + index.getProjectId() + " branch=" + index.getBranchName()
                        + "; retrying next run: " + repositoryFailure.getMessage());
                return;
            }
            if (claimed == 0) {
                // Access or a concurrent build won the atomic registry race.
                continue;
            }

            boolean removed = true;
            boolean physicalDeletionStarted = false;
            boolean deletionOutcomeUncertain = false;
            List<RagBranchIndexGenerationRepository.CleanupGenerationCandidate> generations;
            try {
                generations = generationRepository.findCleanupCandidatesByBranchIndexId(
                        index.getBranchIndexId());
            } catch (RuntimeException repositoryFailure) {
                cancelClaim(index.getBranchIndexId(), cleanupOwner);
                recordDegradedRun("Transient RAG cleanup stopped after a registry read failure; "
                        + "remaining candidates will retry next run: " + repositoryFailure.getMessage());
                return;
            }
            // A readable active generation is deleted last. If an earlier
            // target-specific rejection occurs, releasing the claim is safe as
            // long as no physical target was removed; after a partial cleanup
            // the durable claim stays in place until the next idempotent retry.
            generations = generations.stream()
                    .sorted(Comparator.comparing(generation ->
                            generation.getStatus() == RagBranchIndexGenerationStatus.ACTIVE))
                    .toList();
            for (var generation : generations) {
                if (generation.getStatus() == RagBranchIndexGenerationStatus.ACTIVE
                        && !removed) {
                    // Keep the currently readable target intact if an older
                    // target could not be reconciled. If an older target was
                    // already removed, the claim below remains durable and
                    // prevents reads of that partial generation set.
                    break;
                }
                RagPipelineClient.BranchDeletionOutcome outcome;
                try {
                    if (branchRepository.heartbeatTransientDeletionClaim(
                            index.getBranchIndexId(), cleanupOwner, OffsetDateTime.now()) == 0) {
                        recordDegradedRun("Transient RAG cleanup lost its durable claim for project="
                                + index.getProjectId() + " branch=" + index.getBranchName()
                                + "; remaining targets will retry next run");
                        return;
                    }
                    outcome = pipelineClient.deleteBranchWithOutcome(
                            index.getWorkspaceName(), index.getProjectNamespace(),
                            index.getBranchName(), generation.getCollectionName(),
                            generation.getRevision(), generation.getManifestDigest());
                } catch (RuntimeException unexpectedFailure) {
                    // The request may have reached RAG before the client threw.
                    // Keep the claim until an idempotent retry can reconcile it.
                    recordDegradedRun("Transient RAG cleanup stopped at target="
                            + generation.getCollectionName()
                            + " after an unexpected client failure; remaining generations "
                            + "will retry next run: " + unexpectedFailure.getMessage());
                    return;
                }
                if (outcome.successful()) {
                    physicalDeletionStarted = true;
                }
                if (!outcome.successful()) {
                    if (isRagDisabled(outcome)) {
                        // Deployment-level disablement is intentional. Retain
                        // the registry and avoid an hourly degraded signal.
                        cancelClaim(index.getBranchIndexId(), cleanupOwner);
                        recordHealthyRun();
                        return;
                    }
                    removed = false;
                    deletionOutcomeUncertain |= outcome.failure()
                            == RagPipelineClient.BranchDeletionFailure.TRANSPORT;
                    log.debug("Transient RAG generation cleanup rejected generation={} target={}: "
                                    + "status={} detail={}",
                            generation.getGenerationId(), outcome.targetLabel(),
                            outcome.statusCode() != null ? outcome.statusCode() : outcome.failure(),
                            outcome.detail());
                    if (outcome.shouldStopRemainingTargets()) {
                        if (!physicalDeletionStarted && !deletionOutcomeUncertain) {
                            cancelClaim(index.getBranchIndexId(), cleanupOwner);
                        }
                        recordDegradedRun("Transient RAG cleanup stopped at target="
                                + outcome.targetLabel() + " after " + outcome.failure()
                                + " failure; remaining generations will retry next run: "
                                + outcome.detail());
                        return;
                    }
                }
            }
            if (removed) {
                int deleted;
                try {
                    deleted = branchRepository.deleteClaimedTransientById(
                            index.getBranchIndexId(), cleanupOwner);
                } catch (RuntimeException repositoryFailure) {
                    recordDegradedRun("Transient RAG cleanup could not finalize registry deletion "
                            + "for project=" + index.getProjectId() + " branch="
                            + index.getBranchName() + "; retrying next run: "
                            + repositoryFailure.getMessage());
                    return;
                }
                if (deleted == 0) {
                    // A claimed row cannot be made readable or rebuilt. A zero
                    // delete means registry finalization failed independently;
                    // delete leaves the durable token in place so destroyed data
                    // is never exposed.
                    recordDegradedRun("Transient RAG cleanup deleted physical targets but could not "
                            + "finalize its registry claim for project=" + index.getProjectId()
                            + " branch=" + index.getBranchName() + "; retrying next run");
                    return;
                }
                log.info("Removed expired transient RAG branch index project={}, branch={}",
                        index.getProjectId(), index.getBranchName());
            } else {
                if (!physicalDeletionStarted && !deletionOutcomeUncertain) {
                    cancelClaim(index.getBranchIndexId(), cleanupOwner);
                }
                rejectedCandidates++;
            }
        }
        if (rejectedCandidates > 0) {
            recordDegradedRun("Transient RAG cleanup completed with " + rejectedCandidates
                    + " rejected candidate(s); they will retry next run");
        } else {
            recordHealthyRun();
        }
    }

    private void cancelClaim(long branchIndexId, String claimToken) {
        try {
            branchRepository.cancelTransientDeletion(branchIndexId, claimToken);
        } catch (RuntimeException cancellationFailure) {
            // Keeping the token is fail-safe: no reader will receive a target
            // whose cleanup outcome cannot be reconciled.
            log.debug("Could not release transient cleanup claim branchIndex={}: {}",
                    branchIndexId, cancellationFailure.getMessage());
        }
    }

    private static boolean isRagDisabled(
            RagPipelineClient.BranchDeletionOutcome outcome) {
        return outcome.statusCode() == null
                && outcome.failure() == RagPipelineClient.BranchDeletionFailure.TARGET
                && "RAG disabled".equals(outcome.detail());
    }

    private void recordDegradedRun(String message) {
        if (cleanupState == CleanupState.HEALTHY) {
            cleanupState = CleanupState.DEGRADED;
            log.warn(message);
        } else {
            log.info(message);
        }
    }

    private void recordHealthyRun() {
        if (cleanupState == CleanupState.DEGRADED) {
            cleanupState = CleanupState.HEALTHY;
            log.info("Transient RAG cleanup recovered");
        }
    }

    private enum CleanupState { HEALTHY, DEGRADED }
}
