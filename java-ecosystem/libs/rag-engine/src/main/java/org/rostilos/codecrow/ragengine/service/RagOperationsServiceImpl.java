package org.rostilos.codecrow.ragengine.service;

import org.rostilos.codecrow.analysisapi.rag.RagOperationsService;
import org.rostilos.codecrow.core.model.analysis.AnalysisLockType;
import org.rostilos.codecrow.core.model.analysis.RagIndexStatus;
import org.rostilos.codecrow.core.model.job.Job;
import org.rostilos.codecrow.core.model.job.JobTriggerSource;
import org.rostilos.codecrow.core.model.project.Project;
import org.rostilos.codecrow.core.model.rag.DeltaIndexStatus;
import org.rostilos.codecrow.core.model.rag.RagDeltaIndex;
import org.rostilos.codecrow.core.model.vcs.VcsConnection;
import org.rostilos.codecrow.core.model.vcs.VcsRepoBinding;
import org.rostilos.codecrow.core.persistence.repository.rag.RagDeltaIndexRepository;
import org.rostilos.codecrow.core.service.AnalysisJobService;
import org.rostilos.codecrow.analysisengine.service.AnalysisLockService;
import org.rostilos.codecrow.vcsclient.VcsClient;
import org.rostilos.codecrow.vcsclient.VcsClientProvider;
import org.slf4j.Logger;
import org.slf4j.LoggerFactory;
import org.springframework.beans.factory.annotation.Value;
import org.springframework.stereotype.Service;
import org.springframework.transaction.annotation.Transactional;

import java.io.IOException;
import java.time.OffsetDateTime;
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
    private final RagDeltaIndexRepository ragDeltaIndexRepository;
    private final DeltaIndexService deltaIndexService;
    private final VcsClientProvider vcsClientProvider;

    @Value("${codecrow.rag.api.enabled:false}")
    private boolean ragApiEnabled;

    public RagOperationsServiceImpl(
            RagIndexTrackingService ragIndexTrackingService,
            IncrementalRagUpdateService incrementalRagUpdateService,
            AnalysisLockService analysisLockService,
            AnalysisJobService analysisJobService,
            RagDeltaIndexRepository ragDeltaIndexRepository,
            DeltaIndexService deltaIndexService,
            VcsClientProvider vcsClientProvider
    ) {
        this.ragIndexTrackingService = ragIndexTrackingService;
        this.incrementalRagUpdateService = incrementalRagUpdateService;
        this.analysisLockService = analysisLockService;
        this.analysisJobService = analysisJobService;
        this.ragDeltaIndexRepository = ragDeltaIndexRepository;
        this.deltaIndexService = deltaIndexService;
        this.vcsClientProvider = vcsClientProvider;
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
                
                // Don't pass filesUpdated to markUpdatingCompleted since it's just the delta,
                // not the total file count in the index
                ragIndexTrackingService.markUpdatingCompleted(project, branchName, commitHash);

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
    
    // ==========================================================================
    // DELTA INDEX OPERATIONS
    // ==========================================================================
    
    @Override
    public boolean isDeltaIndexReady(Project project, String branchName) {
        return ragDeltaIndexRepository.existsReadyDeltaIndex(project.getId(), branchName);
    }
    
    @Override
    @Transactional
    public void createOrUpdateDeltaIndex(
            Project project,
            String deltaBranch,
            String baseBranch,
            String deltaCommit,
            String rawDiff,
            Consumer<Map<String, Object>> eventConsumer
    ) {
        if (!isDeltaIndexEnabled(project)) {
            log.debug("Delta indexing not enabled for project={}", project.getId());
            return;
        }
        
        if (!shouldHaveDeltaIndex(project, deltaBranch)) {
            log.debug("Branch {} does not match delta patterns for project={}", deltaBranch, project.getId());
            return;
        }
        
        Job job = null;
        String lockKey = null;
        
        try {
            // Check if base index is ready
            if (!isRagIndexReady(project)) {
                log.warn("Cannot create delta index - base RAG index not ready for project={}", project.getId());
                eventConsumer.accept(Map.of(
                    "type", "warning",
                    "message", "Base RAG index not ready - cannot create delta index"
                ));
                return;
            }
            
            job = analysisJobService.createRagIndexJob(project, false, JobTriggerSource.WEBHOOK);
            analysisJobService.info(job, "delta_init",
                    String.format("Creating delta index for branch '%s' based on '%s'", deltaBranch, baseBranch));
            
            // Parse diff for changed files
            IncrementalRagUpdateService.DiffResult diffResult = incrementalRagUpdateService.parseDiffForRag(rawDiff);
            Set<String> changedFiles = diffResult.addedOrModified();
            
            if (changedFiles.isEmpty()) {
                log.debug("No files changed for delta index - skipping");
                analysisJobService.info(job, "delta_skip", "No files differ from base branch");
                analysisJobService.completeJob(job, null);
                return;
            }
            
            log.info("Creating delta index for project={}, branch={}, {} changed files",
                    project.getId(), deltaBranch, changedFiles.size());
            
            Optional<String> lockKeyOpt = analysisLockService.acquireLock(
                    project,
                    deltaBranch,
                    AnalysisLockType.RAG_INDEXING,
                    deltaCommit,
                    null
            );
            
            if (lockKeyOpt.isEmpty()) {
                log.warn("Delta index update already in progress for project={}, branch={}",
                        project.getId(), deltaBranch);
                analysisJobService.warn(job, "delta_skip", "Delta index update already in progress");
                analysisJobService.failJob(job, "Delta index update in progress");
                return;
            }
            
            lockKey = lockKeyOpt.get();
            
            eventConsumer.accept(Map.of(
                "type", "status",
                "state", "delta_update",
                "message", "Creating delta index with " + changedFiles.size() + " files"
            ));
            
            // Get or create delta index entity
            RagDeltaIndex deltaIndex = ragDeltaIndexRepository
                    .findByProjectIdAndBranchName(project.getId(), deltaBranch)
                    .orElseGet(() -> {
                        RagDeltaIndex newIndex = new RagDeltaIndex();
                        newIndex.setProject(project);
                        newIndex.setBranchName(deltaBranch);
                        // Set temporary collection name - will be updated after RAG pipeline creates the actual collection
                        newIndex.setCollectionName("pending_" + project.getId() + "_delta_" + deltaBranch.replace("/", "_"));
                        return newIndex;
                    });
            
            deltaIndex.setBaseBranch(baseBranch);
            deltaIndex.setDeltaCommitHash(deltaCommit);
            deltaIndex.setStatus(DeltaIndexStatus.CREATING);
            deltaIndex.setUpdatedAt(OffsetDateTime.now());
            deltaIndex = ragDeltaIndexRepository.save(deltaIndex);
            
            // Call RAG pipeline to create delta index
            VcsRepoBinding vcsRepoBinding = project.getVcsRepoBinding();
            if (vcsRepoBinding == null) {
                throw new IllegalStateException("Project has no VcsRepoBinding configured");
            }
            
            Map<String, Object> result = deltaIndexService.createOrUpdateDeltaIndex(
                    project,
                    vcsRepoBinding.getVcsConnection(),
                    vcsRepoBinding.getExternalNamespace(),
                    vcsRepoBinding.getExternalRepoSlug(),
                    deltaBranch,
                    baseBranch,
                    deltaCommit,
                    rawDiff
            );
            
            // Update delta index entity with results
            int chunkCount = (Integer) result.getOrDefault("chunkCount", 0);
            int fileCount = (Integer) result.getOrDefault("fileCount", 0);
            String collectionName = (String) result.get("collectionName");
            String baseCommitHash = (String) result.get("baseCommitHash");
            
            deltaIndex.setChunkCount(chunkCount);
            deltaIndex.setFileCount(fileCount);
            deltaIndex.setCollectionName(collectionName);
            deltaIndex.setBaseCommitHash(baseCommitHash);
            deltaIndex.setStatus(DeltaIndexStatus.READY);
            deltaIndex.setErrorMessage(null);
            deltaIndex.setUpdatedAt(OffsetDateTime.now());
            ragDeltaIndexRepository.save(deltaIndex);
            
            eventConsumer.accept(Map.of(
                "type", "status",
                "state", "delta_complete",
                "message", String.format("Delta index created: %d files, %d chunks", fileCount, chunkCount)
            ));
            
            log.info("Delta index created for project={}, branch={}: {} files, {} chunks",
                    project.getId(), deltaBranch, fileCount, chunkCount);
            analysisJobService.info(job, "delta_complete",
                    String.format("Delta index created: %d files, %d chunks", fileCount, chunkCount));
            analysisJobService.completeJob(job, null);
            
        } catch (Exception e) {
            // Mark delta index as failed
            ragDeltaIndexRepository.findByProjectIdAndBranchName(project.getId(), deltaBranch)
                    .ifPresent(di -> {
                        di.setStatus(DeltaIndexStatus.FAILED);
                        di.setErrorMessage(e.getMessage());
                        di.setUpdatedAt(OffsetDateTime.now());
                        ragDeltaIndexRepository.save(di);
                    });
            
            log.error("Delta index creation failed for project={}, branch={}",
                    project.getId(), deltaBranch, e);
            if (job != null) {
                analysisJobService.error(job, "delta_error", "Delta index failed: " + e.getMessage());
                analysisJobService.failJob(job, e.getMessage());
            }
            eventConsumer.accept(Map.of(
                "type", "warning",
                "state", "delta_error",
                "message", "Delta index creation failed: " + e.getMessage()
            ));
        } finally {
            if (lockKey != null) {
                analysisLockService.releaseLock(lockKey);
            }
        }
    }
    
    @Override
    public boolean ensureDeltaIndexForPrTarget(
            Project project,
            String targetBranch,
            Consumer<Map<String, Object>> eventConsumer
    ) {
        if (!isDeltaIndexEnabled(project)) {
            log.debug("Delta indexing not enabled for project={}", project.getId());
            return false;
        }
        
        if (!shouldHaveDeltaIndex(project, targetBranch)) {
            log.debug("Branch {} does not match delta patterns for project={}", targetBranch, project.getId());
            return false;
        }
        
        // Check if delta index already exists
        if (isDeltaIndexReady(project, targetBranch)) {
            log.debug("Delta index already exists for project={}, branch={}", project.getId(), targetBranch);
            return true;
        }
        
        // Check if base index is ready
        if (!isRagIndexReady(project)) {
            log.warn("Cannot create delta index - base RAG index not ready for project={}", project.getId());
            eventConsumer.accept(Map.of(
                "type", "warning",
                "message", "Base RAG index not ready - cannot create delta index for target branch"
            ));
            return false;
        }
        
        // Get VCS connection info
        VcsRepoBinding vcsRepoBinding = project.getVcsRepoBinding();
        if (vcsRepoBinding == null) {
            log.error("Project has no VcsRepoBinding configured");
            return false;
        }
        
        VcsConnection vcsConnection = vcsRepoBinding.getVcsConnection();
        String workspaceSlug = vcsRepoBinding.getExternalNamespace();
        String repoSlug = vcsRepoBinding.getExternalRepoSlug();
        
        // Get base branch (main branch)
        String baseBranch = project.getConfiguration() != null ? project.getConfiguration().mainBranch() : null;
        if (baseBranch == null || baseBranch.isEmpty()) {
            baseBranch = "main";
        }
        
        // Same branch? No delta needed
        if (targetBranch.equals(baseBranch)) {
            log.debug("Target branch is same as base branch - no delta index needed");
            return false;
        }
        
        try {
            log.info("Creating delta index for PR target branch: project={}, branch={}, baseBranch={}",
                    project.getId(), targetBranch, baseBranch);
            
            eventConsumer.accept(Map.of(
                "type", "status",
                "state", "delta_init",
                "message", String.format("Creating delta index for target branch '%s'", targetBranch)
            ));
            
            // Fetch diff between base branch and target branch
            VcsClient vcsClient = vcsClientProvider.getClient(vcsConnection);
            String rawDiff = vcsClient.getBranchDiff(workspaceSlug, repoSlug, baseBranch, targetBranch);
            
            if (rawDiff == null || rawDiff.isEmpty()) {
                log.debug("No diff between {} and {} - skipping delta index", baseBranch, targetBranch);
                eventConsumer.accept(Map.of(
                    "type", "info",
                    "message", String.format("No changes between %s and %s - delta index not needed", baseBranch, targetBranch)
                ));
                return false;
            }
            
            // Get latest commit hash on target branch for tracking
            String targetCommit = vcsClient.getLatestCommitHash(workspaceSlug, repoSlug, targetBranch);
            
            // Create the delta index using existing method
            createOrUpdateDeltaIndex(project, targetBranch, baseBranch, targetCommit, rawDiff, eventConsumer);
            
            return isDeltaIndexReady(project, targetBranch);
            
        } catch (Exception e) {
            log.error("Failed to create delta index for PR target branch: project={}, branch={}",
                    project.getId(), targetBranch, e);
            eventConsumer.accept(Map.of(
                "type", "warning",
                "state", "delta_error",
                "message", "Failed to create delta index for target branch: " + e.getMessage()
            ));
            return false;
        }
    }
    
    @Override
    public boolean ensureRagIndexUpToDate(
            Project project,
            String targetBranch,
            Consumer<Map<String, Object>> eventConsumer
    ) {
        if (!isRagEnabled(project)) {
            log.debug("RAG not enabled for project={}", project.getId());
            return false;
        }
        
        // Get VCS connection info
        VcsRepoBinding vcsRepoBinding = project.getVcsRepoBinding();
        if (vcsRepoBinding == null) {
            log.error("Project has no VcsRepoBinding configured");
            return false;
        }
        
        VcsConnection vcsConnection = vcsRepoBinding.getVcsConnection();
        String workspaceSlug = vcsRepoBinding.getExternalNamespace();
        String repoSlug = vcsRepoBinding.getExternalRepoSlug();
        
        // Get base branch (main branch)
        String baseBranch = getBaseBranch(project);
        
        try {
            VcsClient vcsClient = vcsClientProvider.getClient(vcsConnection);
            
            // Case 1: Target branch is the main branch - check/update main RAG index
            if (targetBranch.equals(baseBranch)) {
                return ensureMainIndexUpToDate(project, targetBranch, vcsClient, workspaceSlug, repoSlug, eventConsumer);
            }
            
            // Case 2: Target branch should have delta index - check/update delta index
            if (shouldHaveDeltaIndex(project, targetBranch)) {
                // First ensure main index is up to date
                ensureMainIndexUpToDate(project, baseBranch, vcsClient, workspaceSlug, repoSlug, eventConsumer);
                
                // Then ensure delta index exists and is up to date
                return ensureDeltaIndexUpToDate(project, targetBranch, baseBranch, vcsClient, workspaceSlug, repoSlug, eventConsumer);
            }
            
            // Case 3: Target branch has no delta pattern - just check main index is ready
            return isRagIndexReady(project);
            
        } catch (Exception e) {
            log.error("Failed to ensure RAG index up-to-date for project={}, targetBranch={}",
                    project.getId(), targetBranch, e);
            eventConsumer.accept(Map.of(
                "type", "warning",
                "state", "rag_error",
                "message", "Failed to update RAG index: " + e.getMessage()
            ));
            return isRagIndexReady(project); // Return current state even on error
        }
    }
    
    /**
     * Ensures the main RAG index is up-to-date with the current commit on the branch.
     */
    private boolean ensureMainIndexUpToDate(
            Project project,
            String branchName,
            VcsClient vcsClient,
            String workspaceSlug,
            String repoSlug,
            Consumer<Map<String, Object>> eventConsumer
    ) throws IOException {
        if (!isRagIndexReady(project)) {
            log.debug("Main RAG index not ready for project={}", project.getId());
            return false;
        }
        
        // Get current commit on branch
        String currentCommit = vcsClient.getLatestCommitHash(workspaceSlug, repoSlug, branchName);
        
        // Get indexed commit from tracking service
        Optional<RagIndexStatus> indexStatus = ragIndexTrackingService.getIndexStatus(project);
        if (indexStatus.isEmpty()) {
            log.warn("No RAG index status found for project={}", project.getId());
            return false;
        }
        
        String indexedCommit = indexStatus.get().getIndexedCommitHash();
        
        // If commits match, index is up to date
        if (currentCommit.equals(indexedCommit)) {
            log.debug("Main RAG index is up-to-date for project={}, commit={}", project.getId(), currentCommit);
            return true;
        }
        
        log.info("Main RAG index outdated for project={}: indexed={}, current={}", 
                project.getId(), indexedCommit, currentCommit);
        
        // Fetch diff between indexed commit and current commit using branch diff API
        // (works with both branch names and commit hashes)
        String rawDiff = vcsClient.getBranchDiff(workspaceSlug, repoSlug, indexedCommit, currentCommit);
        
        if (rawDiff == null || rawDiff.isEmpty()) {
            log.debug("No diff between {} and {} - index is up to date", indexedCommit, currentCommit);
            // Update the commit hash even if no changes (e.g., merge commits with no file changes)
            ragIndexTrackingService.markUpdatingCompleted(project, branchName, currentCommit);
            return true;
        }
        
        eventConsumer.accept(Map.of(
            "type", "status",
            "state", "rag_update",
            "message", String.format("Updating RAG index from %s to %s", 
                    indexedCommit.substring(0, 7), currentCommit.substring(0, 7))
        ));
        
        // Trigger incremental update
        triggerIncrementalUpdate(project, branchName, currentCommit, rawDiff, eventConsumer);
        
        return isRagIndexReady(project);
    }
    
    /**
     * Ensures the delta index for a branch is up-to-date with the current commit.
     */
    private boolean ensureDeltaIndexUpToDate(
            Project project,
            String targetBranch,
            String baseBranch,
            VcsClient vcsClient,
            String workspaceSlug,
            String repoSlug,
            Consumer<Map<String, Object>> eventConsumer
    ) throws IOException {
        // Check if delta index exists
        Optional<RagDeltaIndex> deltaIndexOpt = ragDeltaIndexRepository
                .findByProjectIdAndBranchName(project.getId(), targetBranch);
        
        // Get current commit on target branch
        String currentCommit = vcsClient.getLatestCommitHash(workspaceSlug, repoSlug, targetBranch);
        
        if (deltaIndexOpt.isEmpty()) {
            // No delta index exists - create it
            log.info("Delta index does not exist for project={}, branch={} - creating", 
                    project.getId(), targetBranch);
            return ensureDeltaIndexForPrTarget(project, targetBranch, eventConsumer);
        }
        
        RagDeltaIndex deltaIndex = deltaIndexOpt.get();
        
        // Check if delta index is in a usable state
        if (deltaIndex.getStatus() != DeltaIndexStatus.READY) {
            log.debug("Delta index not ready for project={}, branch={}, status={}", 
                    project.getId(), targetBranch, deltaIndex.getStatus());
            // Try to recreate
            return ensureDeltaIndexForPrTarget(project, targetBranch, eventConsumer);
        }
        
        String indexedCommit = deltaIndex.getDeltaCommitHash();
        
        // If commits match, index is up to date
        if (currentCommit.equals(indexedCommit)) {
            log.debug("Delta index is up-to-date for project={}, branch={}, commit={}", 
                    project.getId(), targetBranch, currentCommit);
            return true;
        }
        
        log.info("Delta index outdated for project={}, branch={}: indexed={}, current={}", 
                project.getId(), targetBranch, indexedCommit, currentCommit);
        
        // Fetch diff between base branch and current target branch
        String rawDiff = vcsClient.getBranchDiff(workspaceSlug, repoSlug, baseBranch, targetBranch);
        
        if (rawDiff == null || rawDiff.isEmpty()) {
            log.debug("No diff between {} and {} - delta index not needed", baseBranch, targetBranch);
            return true; // No diff means branches are in sync
        }
        
        eventConsumer.accept(Map.of(
            "type", "status",
            "state", "delta_update",
            "message", String.format("Updating delta index for %s from %s to %s", 
                    targetBranch, indexedCommit.substring(0, Math.min(7, indexedCommit.length())), 
                    currentCommit.substring(0, 7))
        ));
        
        // Update delta index
        createOrUpdateDeltaIndex(project, targetBranch, baseBranch, currentCommit, rawDiff, eventConsumer);
        
        return isDeltaIndexReady(project, targetBranch);
    }
}
