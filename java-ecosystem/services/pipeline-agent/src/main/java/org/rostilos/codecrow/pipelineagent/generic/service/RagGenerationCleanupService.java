package org.rostilos.codecrow.pipelineagent.generic.service;

import org.rostilos.codecrow.core.model.rag.RagBranchIndexGenerationStatus;
import org.rostilos.codecrow.core.persistence.repository.project.ProjectRepository;
import org.rostilos.codecrow.core.persistence.repository.rag.RagBranchIndexGenerationRepository;
import org.rostilos.codecrow.ragengine.client.RagPipelineClient;
import org.slf4j.Logger;
import org.slf4j.LoggerFactory;
import org.springframework.scheduling.annotation.Scheduled;
import org.springframework.stereotype.Service;

import java.io.IOException;
import java.time.OffsetDateTime;

@Service
public class RagGenerationCleanupService {
    private static final Logger log = LoggerFactory.getLogger(RagGenerationCleanupService.class);

    private final RagBranchIndexGenerationRepository generations;
    private final ProjectRepository projects;
    private final RagPipelineClient pipeline;

    public RagGenerationCleanupService(
            RagBranchIndexGenerationRepository generations,
            ProjectRepository projects,
            RagPipelineClient pipeline) {
        this.generations = generations;
        this.projects = projects;
        this.pipeline = pipeline;
    }

    @Scheduled(fixedDelayString = "${codecrow.rag.generation-cleanup.interval-ms:21600000}",
            initialDelayString = "${codecrow.rag.generation-cleanup.initial-delay-ms:300000}")
    public void cleanup() {
        if (!pipeline.isEnabled()) {
            return;
        }
        OffsetDateTime cutoff = OffsetDateTime.now().minusDays(1);
        int retired = 0;
        int orphans = 0;
        try {
            for (var candidate : generations.findRetirementCandidates(cutoff)) {
                if (candidate.getStatus() != RagBranchIndexGenerationStatus.RETIRING
                        && generations.beginRetirement(candidate.getGenerationId(), cutoff) == 0) {
                    continue;
                }
                if (delete(candidate.getWorkspaceName(), candidate.getProjectNamespace(),
                        candidate.getBranchName(), candidate.getCollectionName(),
                        candidate.getRevision(), candidate.getManifestDigest())) {
                    retired += generations.finishRetirement(candidate.getGenerationId());
                }
            }
            for (var candidate : pipeline.listOrphanCandidates()) {
                if (!projects.existsById(candidate.projectId())
                        && !generations.existsByCollectionName(candidate.collectionTarget())
                        && delete(candidate.workspace(), candidate.project(), candidate.branch(),
                                candidate.collectionTarget(), candidate.revision(), candidate.manifestDigest())) {
                    orphans++;
                }
            }
        } catch (IOException | RuntimeException exception) {
            log.warn("Structural generation cleanup stopped and will retry: {}", exception.toString());
        }
        if (retired + orphans > 0) {
            log.info("Structural generation cleanup retired {} superseded and {} orphaned targets",
                    retired, orphans);
        }
    }

    private boolean delete(String workspace, String project, String branch,
            String target, String revision, String manifest) throws IOException {
        var outcome = pipeline.deleteBranchWithOutcome(workspace, project, branch, target, revision, manifest);
        if (outcome.successful()) {
            return true;
        }
        if (outcome.shouldStopRemainingTargets() && !Integer.valueOf(409).equals(outcome.statusCode())) {
            throw new IOException("RAG deletion unavailable for " + target + ": " + outcome.detail());
        }
        if (Integer.valueOf(423).equals(outcome.statusCode())) {
            log.debug("Structural generation cleanup deferred for active reader target={}", target);
        } else {
            log.warn("Structural generation cleanup deferred target={}: status={}, detail={}",
                    target, outcome.statusCode(), outcome.detail());
        }
        return false;
    }
}
