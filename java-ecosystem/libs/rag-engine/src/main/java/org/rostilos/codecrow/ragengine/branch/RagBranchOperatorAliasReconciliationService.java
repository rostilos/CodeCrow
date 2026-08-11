package org.rostilos.codecrow.ragengine.branch;

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
    private static final Logger log = LoggerFactory.getLogger(
            RagBranchOperatorAliasReconciliationService.class);

    private final RagBranchIndexRepository branchIndexRepository;
    private final RagPipelineClient pipelineClient;

    public RagBranchOperatorAliasReconciliationService(
            RagBranchIndexRepository branchIndexRepository,
            RagPipelineClient pipelineClient) {
        this.branchIndexRepository = branchIndexRepository;
        this.pipelineClient = pipelineClient;
    }

    @Scheduled(
            fixedDelayString = "${codecrow.rag.operator-alias.reconcile-interval-ms:300000}",
            initialDelayString = "${codecrow.rag.operator-alias.reconcile-initial-delay-ms:15000}")
    public void reconcileActiveGenerationAliases() {
        for (var candidate : branchIndexRepository.findOperatorAliasCandidates()) {
            try {
                pipelineClient.publishGenerationAliases(
                        candidate.getWorkspaceName(),
                        candidate.getProjectNamespace(),
                        candidate.getBranchName(),
                        candidate.getRevision(),
                        candidate.getCollectionName(),
                        true,
                        candidate.getIndexKind() == RagBranchIndexKind.PRIMARY);
            } catch (Exception failure) {
                log.warn(
                        "Could not reconcile readable RAG alias for project={} branch={}: {}",
                        candidate.getProjectId(),
                        candidate.getBranchName(),
                        failure.getMessage());
            }
        }
    }
}
