package org.rostilos.codecrow.ragengine.branch;

import org.rostilos.codecrow.core.model.rag.RagBranchIndex;
import org.rostilos.codecrow.core.model.rag.RagBranchIndexKind;
import org.rostilos.codecrow.core.persistence.repository.rag.RagBranchIndexRepository;
import org.rostilos.codecrow.ragengine.client.RagPipelineClient;
import org.slf4j.Logger;
import org.slf4j.LoggerFactory;
import org.springframework.scheduling.annotation.Scheduled;
import org.springframework.stereotype.Service;
import org.springframework.transaction.annotation.Transactional;

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
    @Transactional(readOnly = true)
    public void reconcileActiveGenerationAliases() {
        for (RagBranchIndex branchIndex : branchIndexRepository.findAll()) {
            if (!isPublishedOperatorBranch(branchIndex)) {
                continue;
            }
            var generation = branchIndex.getActiveGeneration();
            var project = branchIndex.getProject();
            try {
                pipelineClient.publishGenerationAliases(
                        project.getWorkspace().getName(),
                        project.getNamespace(),
                        branchIndex.getBranchName(),
                        generation.getRevision(),
                        generation.getCollectionName(),
                        true,
                        branchIndex.getIndexKind() == RagBranchIndexKind.PRIMARY);
            } catch (Exception failure) {
                log.warn(
                        "Could not reconcile readable RAG alias for project={} branch={}: {}",
                        project.getId(),
                        branchIndex.getBranchName(),
                        failure.getMessage());
            }
        }
    }

    private static boolean isPublishedOperatorBranch(RagBranchIndex branchIndex) {
        if (branchIndex == null || branchIndex.getActiveGeneration() == null) {
            return false;
        }
        return branchIndex.getIndexKind() == RagBranchIndexKind.PRIMARY
                || branchIndex.getIndexKind() == RagBranchIndexKind.DURABLE;
    }
}
