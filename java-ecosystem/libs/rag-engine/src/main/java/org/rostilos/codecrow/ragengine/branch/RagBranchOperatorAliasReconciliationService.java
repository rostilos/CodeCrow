package org.rostilos.codecrow.ragengine.branch;

import java.io.IOException;
import java.util.List;
import java.util.Objects;
import java.util.Optional;

import org.rostilos.codecrow.core.model.rag.RagBranchIndexKind;
import org.rostilos.codecrow.core.persistence.repository.rag.RagBranchIndexRepository;
import org.rostilos.codecrow.ragengine.client.RagPipelineClient;
import org.slf4j.Logger;
import org.slf4j.LoggerFactory;
import org.springframework.scheduling.annotation.Scheduled;
import org.springframework.stereotype.Service;

/**
 * Restores human-readable Qdrant aliases for already active branch generations.
 *
 * Normal generation activation publishes those aliases atomically alongside its
 * immutable target. This independent, idempotent repair loop makes the feature
 * safe for projects and Qdrant snapshots created before that contract existed.
 * It never changes the registry or analysis binding, so a temporary Qdrant
 * failure only affects operator discoverability and is retried later.
 */
@Service
public class RagBranchOperatorAliasReconciliationService {
    private static final int MAX_GENERATION_REFRESHES = 3;
    private static final Logger log = LoggerFactory.getLogger(
            RagBranchOperatorAliasReconciliationService.class);

    private final RagBranchIndexRepository branchIndexRepository;
    private final RagPipelineClient pipelineClient;
    private ReconciliationState reconciliationState = ReconciliationState.HEALTHY;

    public RagBranchOperatorAliasReconciliationService(
            RagBranchIndexRepository branchIndexRepository,
            RagPipelineClient pipelineClient) {
        this.branchIndexRepository = branchIndexRepository;
        this.pipelineClient = pipelineClient;
    }

    @Scheduled(
            fixedDelayString = "${codecrow.rag.operator-alias.reconcile-interval-ms:300000}",
            initialDelayString = "${codecrow.rag.operator-alias.reconcile-initial-delay-ms:15000}")
    public synchronized void reconcileActiveGenerationAliases() {
        List<RagBranchIndexRepository.OperatorAliasCandidate> candidates;
        try {
            candidates = branchIndexRepository.findOperatorAliasCandidates();
        } catch (RuntimeException repositoryFailure) {
            recordDegradedRun("Could not read active RAG aliases for reconciliation; "
                    + "retrying next run: " + repositoryFailure.getMessage());
            return;
        }

        int candidateRejections = 0;
        for (var candidate : candidates) {
            try {
                publishCurrentGeneration(candidate);
            } catch (RagPipelineClient.RagApiException apiFailure) {
                if (apiFailure.isServiceFailure()) {
                    recordDegradedRun("RAG alias reconciliation stopped after a service failure; "
                            + "remaining candidates will retry next run: " + apiFailure.getMessage());
                    return;
                }
                candidateRejections++;
                log.debug(
                        "RAG alias candidate was rejected for project={} branch={}; continuing: {}",
                        candidate.getProjectId(),
                        candidate.getBranchName(),
                        apiFailure.getMessage());
            } catch (IOException transportFailure) {
                // One unavailable RAG service would make every remaining call
                // fail and generate the same warning. Stop this bounded run;
                // the scheduler's fixed delay is the retry backoff.
                recordDegradedRun("RAG alias reconciliation stopped after a transport failure; "
                        + "remaining candidates will retry next run: " + transportFailure.getMessage());
                return;
            } catch (CandidateRegistryReadException repositoryFailure) {
                recordDegradedRun("RAG alias reconciliation stopped after a registry read failure; "
                        + "remaining candidates will retry next run: " + repositoryFailure.getMessage());
                return;
            } catch (RuntimeException candidateFailure) {
                candidateRejections++;
                log.debug(
                        "RAG alias candidate failed for project={} branch={}; continuing: {}",
                        candidate.getProjectId(),
                        candidate.getBranchName(),
                        candidateFailure.getMessage());
            }
        }
        if (candidateRejections > 0) {
            recordDegradedRun("RAG alias reconciliation completed with " + candidateRejections
                    + " rejected candidate(s); they will retry next run");
        } else {
            recordHealthyRun();
        }
    }

    private void publishCurrentGeneration(
            RagBranchIndexRepository.OperatorAliasCandidate scheduledCandidate) throws IOException {
        RagBranchIndexRepository.OperatorAliasCandidate current = refreshCandidate(scheduledCandidate)
                .orElse(null);
        if (current == null) {
            return;
        }

        for (int refresh = 0; refresh < MAX_GENERATION_REFRESHES; refresh++) {
            publish(current);
            Optional<RagBranchIndexRepository.OperatorAliasCandidate> afterPublication =
                    refreshCandidate(current);
            if (afterPublication.isEmpty()) {
                return;
            }
            if (sameGeneration(current, afterPublication.get())) {
                return;
            }
            log.info("RAG active generation changed during alias reconciliation for "
                            + "project={} branch={}; publishing the current generation instead",
                    current.getProjectId(), current.getBranchName());
            current = afterPublication.get();
        }

        throw new IllegalStateException(
                "RAG active generation kept changing during alias reconciliation for project="
                        + current.getProjectId() + " branch=" + current.getBranchName());
    }

    private Optional<RagBranchIndexRepository.OperatorAliasCandidate> refreshCandidate(
            RagBranchIndexRepository.OperatorAliasCandidate candidate) {
        try {
            return branchIndexRepository.findOperatorAliasCandidateById(candidate.getBranchIndexId());
        } catch (RuntimeException repositoryFailure) {
            throw new CandidateRegistryReadException(repositoryFailure);
        }
    }

    private void publish(RagBranchIndexRepository.OperatorAliasCandidate candidate) throws IOException {
        pipelineClient.publishGenerationAliases(
                candidate.getWorkspaceName(),
                candidate.getProjectNamespace(),
                candidate.getBranchName(),
                candidate.getRevision(),
                candidate.getCollectionName(),
                candidate.getManifestDigest(),
                true,
                candidate.getIndexKind() == RagBranchIndexKind.PRIMARY);
    }

    private static boolean sameGeneration(
            RagBranchIndexRepository.OperatorAliasCandidate first,
            RagBranchIndexRepository.OperatorAliasCandidate second) {
        if (first.getGenerationId() != null || second.getGenerationId() != null) {
            return Objects.equals(first.getGenerationId(), second.getGenerationId());
        }
        return Objects.equals(first.getCollectionName(), second.getCollectionName())
                && Objects.equals(first.getManifestDigest(), second.getManifestDigest());
    }

    private void recordDegradedRun(String message) {
        if (reconciliationState == ReconciliationState.HEALTHY) {
            reconciliationState = ReconciliationState.DEGRADED;
            log.warn(message);
        } else {
            log.info(message);
        }
    }

    private void recordHealthyRun() {
        if (reconciliationState == ReconciliationState.DEGRADED) {
            reconciliationState = ReconciliationState.HEALTHY;
            log.info("RAG alias reconciliation recovered");
        }
    }

    private enum ReconciliationState { HEALTHY, DEGRADED }

    private static final class CandidateRegistryReadException extends RuntimeException {
        private CandidateRegistryReadException(RuntimeException cause) {
            super(cause.getMessage(), cause);
        }
    }
}
