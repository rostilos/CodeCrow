package org.rostilos.codecrow.ragengine.branch;

import org.rostilos.codecrow.core.model.rag.RagBranchIndex;
import org.rostilos.codecrow.core.model.rag.RagBranchIndexKind;
import org.rostilos.codecrow.core.persistence.repository.rag.RagBranchIndexGenerationRepository;
import org.rostilos.codecrow.core.persistence.repository.rag.RagBranchIndexRepository;
import org.rostilos.codecrow.ragengine.client.RagPipelineClient;
import org.slf4j.Logger;
import org.slf4j.LoggerFactory;
import org.springframework.scheduling.annotation.Scheduled;
import org.springframework.stereotype.Service;
import org.springframework.transaction.annotation.Transactional;

import java.time.OffsetDateTime;

/** Removes only expired PR-target generations explicitly classified transient. */
@Service
public class RagTransientBranchIndexCleanupService {
    private static final Logger log = LoggerFactory.getLogger(
            RagTransientBranchIndexCleanupService.class);

    private final RagBranchIndexRepository branchRepository;
    private final RagBranchIndexGenerationRepository generationRepository;
    private final RagPipelineClient pipelineClient;

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
    @Transactional
    public void cleanupExpired() {
        OffsetDateTime now = OffsetDateTime.now();
        for (RagBranchIndex index : branchRepository.findByIndexKind(
                RagBranchIndexKind.TRANSIENT)) {
            var project = index.getProject();
            var config = project.getConfiguration() != null
                    ? project.getConfiguration().ragConfig()
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

            boolean removed = true;
            for (var generation : generationRepository
                    .findByBranchIndexIdOrderByCreatedAtDesc(index.getId())) {
                try {
                    removed &= pipelineClient.deleteBranch(
                            project.getWorkspace().getName(), project.getNamespace(),
                            index.getBranchName(), generation.getCollectionName());
                } catch (Exception failure) {
                    removed = false;
                    log.warn("Failed to clean transient RAG generation {}: {}",
                            generation.getId(), failure.getMessage());
                }
            }
            if (removed) {
                branchRepository.delete(index);
                log.info("Removed expired transient RAG branch index project={}, branch={}",
                        project.getId(), index.getBranchName());
            }
        }
    }
}
