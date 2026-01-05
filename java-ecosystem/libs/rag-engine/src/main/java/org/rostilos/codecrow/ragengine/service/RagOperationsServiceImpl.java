package org.rostilos.codecrow.ragengine.service;

import org.rostilos.codecrow.analysisengine.service.rag.RagOperationsService;
import org.rostilos.codecrow.core.model.analysis.AnalysisLockType;
import org.rostilos.codecrow.core.model.job.Job;
import org.rostilos.codecrow.core.model.job.JobTriggerSource;
import org.rostilos.codecrow.core.model.project.Project;
import org.rostilos.codecrow.core.model.vcs.VcsConnection;
import org.rostilos.codecrow.core.model.vcs.VcsRepoBinding;
import org.rostilos.codecrow.core.service.AnalysisJobService;
import org.rostilos.codecrow.analysisengine.service.AnalysisLockService;
import org.slf4j.Logger;
import org.slf4j.LoggerFactory;
import org.springframework.beans.factory.annotation.Value;
import org.springframework.stereotype.Service;

import java.util.Map;
import java.util.Optional;
import java.util.Set;
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
            String rawDiff,
            Consumer<Map<String, Object>> eventConsumer
    ) {
        Job job = null;
        try {
            if (!incrementalRagUpdateService.shouldPerformIncrementalUpdate(project)) {
                log.debug("Skipping RAG incremental update - not enabled or not yet indexed");
                return;
            }

            // Parse the diff to find changed files
            IncrementalRagUpdateService.DiffResult diffResult = incrementalRagUpdateService.parseDiffForRag(rawDiff);
            Set<String> addedOrModifiedFiles = diffResult.addedOrModified();
            Set<String> deletedFiles = diffResult.deleted();
            
            if (addedOrModifiedFiles.isEmpty() && deletedFiles.isEmpty()) {
                log.debug("Skipping RAG incremental update - no files changed in diff");
                return;
            }
            
            log.info("RAG incremental update: {} files to add/update, {} files to delete",
                    addedOrModifiedFiles.size(), deletedFiles.size());

            job = analysisJobService.createRagIndexJob(project, false, JobTriggerSource.WEBHOOK);
            analysisJobService.info(job, "rag_init",
                    String.format("Starting incremental RAG update for branch '%s' (commit: %s) - %d files to update, %d to delete", 
                            branchName, commitHash, addedOrModifiedFiles.size(), deletedFiles.size()));

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
                        "message", "Updating RAG index with " + (addedOrModifiedFiles.size() + deletedFiles.size()) + " changed files"
                ));

                ragIndexTrackingService.markUpdatingStarted(project, branchName, commitHash);

                log.info("Performing RAG incremental update for project={}, branch={}, commit={}",
                        project.getId(), branchName, commitHash);
                
                // Get VCS connection info from project
                VcsRepoBinding vcsRepoBinding = project.getVcsRepoBinding();
                if (vcsRepoBinding == null) {
                    throw new IllegalStateException("Project has no VcsRepoBinding configured");
                }
                
                VcsConnection vcsConnection = vcsRepoBinding.getVcsConnection();
                String workspaceSlug = vcsRepoBinding.getExternalNamespace();
                String repoSlug = vcsRepoBinding.getExternalRepoSlug();
                
                // Perform the actual incremental update
                Map<String, Object> result = incrementalRagUpdateService.performIncrementalUpdate(
                        project,
                        vcsConnection,
                        workspaceSlug,
                        repoSlug,
                        branchName,
                        commitHash,
                        addedOrModifiedFiles,
                        deletedFiles
                );
                
                int filesUpdated = (Integer) result.getOrDefault("updatedFiles", 0);
                int filesDeleted = (Integer) result.getOrDefault("deletedFiles", 0);
                
                ragIndexTrackingService.markUpdatingCompleted(project, branchName, commitHash, filesUpdated);

                eventConsumer.accept(Map.of(
                        "type", "status",
                        "state", "rag_complete",
                        "message", String.format("RAG index updated: %d files updated, %d deleted", filesUpdated, filesDeleted)
                ));

                log.info("RAG incremental update completed for project={}: {} files updated, {} deleted",
                        project.getId(), filesUpdated, filesDeleted);
                analysisJobService.info(job, "rag_complete", 
                        String.format("RAG incremental update completed: %d files updated, %d deleted", filesUpdated, filesDeleted));
                analysisJobService.completeJob(job, null);

            } catch (Exception e) {
                ragIndexTrackingService.markIndexingFailed(project, e.getMessage());
                log.error("RAG incremental update failed", e);
                if (job != null) {
                    analysisJobService.error(job, "rag_error", "RAG incremental update failed: " + e.getMessage());
                    analysisJobService.failJob(job, "RAG incremental update failed: " + e.getMessage());
                }
                eventConsumer.accept(Map.of(
                        "type", "warning",
                        "state", "rag_error",
                        "message", "RAG incremental update failed: " + e.getMessage()
                ));
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
