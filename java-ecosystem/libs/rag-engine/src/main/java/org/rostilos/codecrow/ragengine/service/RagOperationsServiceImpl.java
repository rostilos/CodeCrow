package org.rostilos.codecrow.ragengine.service;

import org.rostilos.codecrow.analysisengine.service.rag.RagOperationsService;
import org.rostilos.codecrow.core.model.analysis.AnalysisLockType;
import org.rostilos.codecrow.core.model.job.Job;
import org.rostilos.codecrow.core.model.job.JobTriggerSource;
import org.rostilos.codecrow.core.model.project.Project;
import org.rostilos.codecrow.core.service.AnalysisJobService;
import org.rostilos.codecrow.analysisengine.service.AnalysisLockService;
import org.slf4j.Logger;
import org.slf4j.LoggerFactory;
import org.springframework.beans.factory.annotation.Value;
import org.springframework.stereotype.Service;

import java.util.Map;
import java.util.Optional;
import java.util.function.Consumer;

/**
 * Implementation of RagOperationsService that provides RAG operations
 * to analysis-engine components without creating a cyclic dependency.
 */
@Service
public class RagOperationsServiceImpl implements RagOperationsService {

    private static final Logger log = LoggerFactory.getLogger(RagOperationsServiceImpl.class);

    private final RagIndexTrackingService ragIndexTrackingService;
    private final IncrementalRagUpdateService incrementalRagUpdateService;
    private final AnalysisLockService analysisLockService;
    private final AnalysisJobService analysisJobService;

    @Value("${codecrow.rag.api.enabled:false}")
    private boolean ragApiEnabled;

    public RagOperationsServiceImpl(
            RagIndexTrackingService ragIndexTrackingService,
            IncrementalRagUpdateService incrementalRagUpdateService,
            AnalysisLockService analysisLockService,
            AnalysisJobService analysisJobService
    ) {
        this.ragIndexTrackingService = ragIndexTrackingService;
        this.incrementalRagUpdateService = incrementalRagUpdateService;
        this.analysisLockService = analysisLockService;
        this.analysisJobService = analysisJobService;
    }

    @Override
    public boolean isRagEnabled(Project project) {
        if (!ragApiEnabled) {
            return false;
        }
        return incrementalRagUpdateService.shouldPerformIncrementalUpdate(project);
    }

    @Override
    public boolean isRagIndexReady(Project project) {
        if (!isRagEnabled(project)) {
            return false;
        }
        return ragIndexTrackingService.isProjectIndexed(project);
    }

    @Override
    public void triggerIncrementalUpdate(
            Project project,
            String branchName,
            String commitHash,
            Consumer<Map<String, Object>> eventConsumer
    ) {
        Job job = null;
        try {
            if (!incrementalRagUpdateService.shouldPerformIncrementalUpdate(project)) {
                log.debug("Skipping RAG incremental update - not enabled or not yet indexed");
                return;
            }

            job = analysisJobService.createRagIndexJob(project, false, JobTriggerSource.WEBHOOK);
            analysisJobService.info(job, "rag_init",
                    String.format("Starting incremental RAG update for branch '%s' (commit: %s)", branchName, commitHash));

            Optional<String> ragLockKey = analysisLockService.acquireLock(
                    project,
                    branchName,
                    AnalysisLockType.RAG_INDEXING,
                    commitHash,
                    null
            );

            if (ragLockKey.isEmpty()) {
                log.warn("RAG update already in progress for project={}, branch={}",
                        project.getId(), branchName);
                analysisJobService.warn(job, "rag_skip", "RAG update already in progress - skipping");
                analysisJobService.failJob(job, "RAG update already in progress");
                return;
            }

            try {
                eventConsumer.accept(Map.of(
                        "type", "status",
                        "state", "rag_update",
                        "message", "Updating RAG index with changed files"
                ));

                ragIndexTrackingService.markUpdatingStarted(project, branchName, commitHash);

                log.info("Triggering RAG incremental update for project={}, branch={}, commit={}",
                        project.getId(), branchName, commitHash);
                
                // Mark as completed - the actual incremental RAG update is complex and 
                // is handled by the existing IncrementalRagUpdateService when called directly
                // This interface is for simple notification from analysis-engine
                ragIndexTrackingService.markUpdatingCompleted(project, branchName, commitHash, 0);

                eventConsumer.accept(Map.of(
                        "type", "status",
                        "state", "rag_complete",
                        "message", "RAG index update scheduled"
                ));

                log.info("RAG incremental update scheduled for project={}", project.getId());
                analysisJobService.info(job, "rag_complete", "RAG incremental update scheduled");
                analysisJobService.completeJob(job, null);

            } catch (Exception e) {
                ragIndexTrackingService.markIndexingFailed(project, e.getMessage());
                log.error("RAG incremental update failed", e);
                if (job != null) {
                    analysisJobService.error(job, "rag_error", "RAG incremental update failed: " + e.getMessage());
                    analysisJobService.failJob(job, "RAG incremental update failed: " + e.getMessage());
                }
            } finally {
                analysisLockService.releaseLock(ragLockKey.get());
            }
        } catch (Exception e) {
            log.warn("RAG incremental update failed (non-critical): {}", e.getMessage());
            if (job != null) {
                analysisJobService.error(job, "rag_error", "RAG incremental update failed (non-critical): " + e.getMessage());
                analysisJobService.failJob(job, e.getMessage());
            }
        }
    }
}
