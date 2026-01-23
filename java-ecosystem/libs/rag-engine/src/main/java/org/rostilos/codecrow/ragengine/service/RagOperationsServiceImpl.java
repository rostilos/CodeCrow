package org.rostilos.codecrow.ragengine.service;

import org.rostilos.codecrow.analysisapi.rag.RagOperationsService;
import org.rostilos.codecrow.core.model.analysis.AnalysisLockType;
import org.rostilos.codecrow.core.model.analysis.RagIndexStatus;
import org.rostilos.codecrow.core.model.job.Job;
import org.rostilos.codecrow.core.model.job.JobTriggerSource;
import org.rostilos.codecrow.core.model.project.Project;
import org.rostilos.codecrow.core.model.rag.RagBranchIndex;
import org.rostilos.codecrow.core.model.vcs.VcsConnection;
import org.rostilos.codecrow.core.model.vcs.VcsRepoBinding;
import org.rostilos.codecrow.core.persistence.repository.rag.RagBranchIndexRepository;
import org.rostilos.codecrow.core.service.AnalysisJobService;
import org.rostilos.codecrow.analysisengine.service.AnalysisLockService;
import org.rostilos.codecrow.ragengine.client.RagPipelineClient;
import org.rostilos.codecrow.vcsclient.VcsClient;
import org.rostilos.codecrow.vcsclient.VcsClientProvider;
import org.slf4j.Logger;
import org.slf4j.LoggerFactory;
import org.springframework.beans.factory.annotation.Value;
import org.springframework.stereotype.Service;
import org.springframework.transaction.annotation.Transactional;

import java.io.IOException;
import java.time.OffsetDateTime;
import java.util.ArrayList;
import java.util.HashSet;
import java.util.List;
import java.util.Map;
import java.util.Optional;
import java.util.Set;
import java.util.function.Consumer;

/**
 * Implementation of RagOperationsService using single-collection-per-project architecture.
 * 
 * Each project has ONE Qdrant collection containing all branches.
 * Branch is stored as metadata in each point, allowing multi-branch queries.
 */
@Service
public class RagOperationsServiceImpl implements RagOperationsService {

    private static final Logger log = LoggerFactory.getLogger(RagOperationsServiceImpl.class);

    private final RagIndexTrackingService ragIndexTrackingService;
    private final IncrementalRagUpdateService incrementalRagUpdateService;
    private final AnalysisLockService analysisLockService;
    private final AnalysisJobService analysisJobService;
    private final RagBranchIndexRepository ragBranchIndexRepository;
    private final VcsClientProvider vcsClientProvider;
    private final RagPipelineClient ragPipelineClient;

    @Value("${codecrow.rag.api.enabled:false}")
    private boolean ragApiEnabled;

    public RagOperationsServiceImpl(
            RagIndexTrackingService ragIndexTrackingService,
            IncrementalRagUpdateService incrementalRagUpdateService,
            AnalysisLockService analysisLockService,
            AnalysisJobService analysisJobService,
            RagBranchIndexRepository ragBranchIndexRepository,
            VcsClientProvider vcsClientProvider,
            RagPipelineClient ragPipelineClient
    ) {
        this.ragIndexTrackingService = ragIndexTrackingService;
        this.incrementalRagUpdateService = incrementalRagUpdateService;
        this.analysisLockService = analysisLockService;
        this.analysisJobService = analysisJobService;
        this.ragBranchIndexRepository = ragBranchIndexRepository;
        this.vcsClientProvider = vcsClientProvider;
        this.ragPipelineClient = ragPipelineClient;
    }

    @Override
    public boolean isRagEnabled(Project project) {
        if (!ragApiEnabled) {
            return false;
        }
        // Check only if RAG is enabled in project config, not if it's indexed
        var config = project.getConfiguration();
        return config != null && config.ragConfig() != null && config.ragConfig().enabled();
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
        log.info("triggerIncrementalUpdate called for project={}, branch={}, commit={}, diffLength={}",
                project.getId(), branchName, commitHash, rawDiff != null ? rawDiff.length() : 0);
        try {
            if (!incrementalRagUpdateService.shouldPerformIncrementalUpdate(project)) {
                log.info("Skipping RAG incremental update for project={}, branch={} - RAG not enabled or main branch not yet indexed",
                        project.getId(), branchName);
                eventConsumer.accept(Map.of(
                        "type", "info",
                        "message", "Skipping RAG update - main branch must be indexed first"
                ));
                return;
            }
            
            log.info("shouldPerformIncrementalUpdate returned true, parsing diff...");

            // Parse the diff to find changed files
            IncrementalRagUpdateService.DiffResult diffResult = incrementalRagUpdateService.parseDiffForRag(rawDiff);
            Set<String> addedOrModifiedFiles = diffResult.addedOrModified();
            Set<String> deletedFiles = diffResult.deleted();
            
            log.info("Diff parsed: addedOrModified={}, deleted={}", addedOrModifiedFiles, deletedFiles);
            
            if (addedOrModifiedFiles.isEmpty() && deletedFiles.isEmpty()) {
                log.info("Skipping RAG incremental update - no files changed in diff");
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
                
                ragIndexTrackingService.markUpdatingCompleted(project, branchName, commitHash);
                
                // Track branch index for deleted files
                trackBranchIndex(project, branchName, commitHash, deletedFiles);

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
    // BRANCH INDEX TRACKING
    // ==========================================================================
    
    /**
     * Track branch index state including deleted files for query-time filtering.
     */
    @Transactional
    protected void trackBranchIndex(Project project, String branchName, String commitHash, Set<String> deletedFiles) {
        try {
            RagBranchIndex branchIndex = ragBranchIndexRepository
                    .findByProjectIdAndBranchName(project.getId(), branchName)
                    .orElseGet(() -> {
                        RagBranchIndex newIndex = new RagBranchIndex();
                        newIndex.setProject(project);
                        newIndex.setBranchName(branchName);
                        return newIndex;
                    });
            
            branchIndex.setCommitHash(commitHash);
            branchIndex.setUpdatedAt(OffsetDateTime.now());
            
            // Merge deleted files (accumulate across updates)
            if (deletedFiles != null && !deletedFiles.isEmpty()) {
                Set<String> existingDeleted = branchIndex.getDeletedFiles();
                if (existingDeleted != null) {
                    existingDeleted.addAll(deletedFiles);
                    branchIndex.setDeletedFiles(existingDeleted);
                } else {
                    branchIndex.setDeletedFiles(deletedFiles);
                }
            }
            
            ragBranchIndexRepository.save(branchIndex);
        } catch (Exception e) {
            log.warn("Failed to track branch index: {}", e.getMessage());
        }
    }
    
    /**
     * Get deleted files for a branch (for query-time filtering).
     */
    public Set<String> getDeletedFilesForBranch(Project project, String branchName) {
        return ragBranchIndexRepository.findByProjectIdAndBranchName(project.getId(), branchName)
                .map(RagBranchIndex::getDeletedFiles)
                .orElse(Set.of());
    }
    
    @Override
    public boolean isBranchIndexReady(Project project, String branchName) {
        // With single-collection architecture, we check if branch has indexed data
        return ragBranchIndexRepository.existsByProjectIdAndBranchName(project.getId(), branchName);
    }
    
    @Override
    @Transactional
    public void createOrUpdateBranchIndex(
            Project project,
            String branchName,
            String baseBranch,
            String branchCommit,
            String rawDiff,
            Consumer<Map<String, Object>> eventConsumer
    ) {
        // With single-collection architecture, we just do incremental update
        // No separate collection needed - branch data goes into shared collection
        triggerIncrementalUpdate(project, branchName, branchCommit, rawDiff, eventConsumer);
    }
    
    @Override
    public boolean updateBranchIndex(
            Project project,
            String targetBranch,
            Consumer<Map<String, Object>> eventConsumer
    ) {
        // Update branch index - calculates diff between base branch and target branch
        // Unlike ensureBranchIndexForPrTarget, this always recalculates the full diff
        
        if (!isRagEnabled(project)) {
            log.debug("RAG not enabled for project={}", project.getId());
            return false;
        }
        
        if (!isRagIndexReady(project)) {
            log.warn("Cannot update branch index - base RAG index not ready for project={}", project.getId());
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
        
        String baseBranch = getBaseBranch(project);
        
        if (targetBranch.equals(baseBranch)) {
            log.debug("Target branch is same as base branch - no branch index needed");
            return true;
        }
        
        try {
            VcsClient vcsClient = vcsClientProvider.getClient(vcsConnection);
            
            log.info("Updating branch index for project={}, branch={} (diff vs {})", 
                    project.getId(), targetBranch, baseBranch);
            
            eventConsumer.accept(Map.of(
                "type", "status",
                "state", "branch_index",
                "message", String.format("Calculating diff between '%s' and '%s'", baseBranch, targetBranch)
            ));
            
            // Always get fresh diff between base branch and target branch
            String rawDiff = vcsClient.getBranchDiff(workspaceSlug, repoSlug, baseBranch, targetBranch);
            
            if (rawDiff == null || rawDiff.isEmpty()) {
                log.info("No diff between {} and {} - branch has same content as base", baseBranch, targetBranch);
                eventConsumer.accept(Map.of(
                    "type", "info",
                    "message", String.format("Branch '%s' has same content as '%s'", targetBranch, baseBranch)
                ));
                return true;
            }
            
            String targetCommit = vcsClient.getLatestCommitHash(workspaceSlug, repoSlug, targetBranch);
            
            log.info("Branch diff found: {} bytes, triggering incremental update for branch={}, commit={}",
                    rawDiff.length(), targetBranch, targetCommit);
            
            // Trigger incremental update with full branch diff
            triggerIncrementalUpdate(project, targetBranch, targetCommit, rawDiff, eventConsumer);
            
            return true;
            
        } catch (Exception e) {
            log.error("Failed to update branch index for project={}, branch={}", 
                    project.getId(), targetBranch, e);
            eventConsumer.accept(Map.of(
                "type", "error",
                "message", "Failed to update branch index: " + e.getMessage()
            ));
            return false;
        }
    }
    
    @Override
    public boolean ensureBranchIndexForPrTarget(
            Project project,
            String targetBranch,
            Consumer<Map<String, Object>> eventConsumer
    ) {
        // With single-collection architecture, we check if branch has any indexed data
        // If not, we need to index the branch
        
        log.info("ensureBranchIndexForPrTarget called for project={}, branch={}", project.getId(), targetBranch);
        
        if (!isRagEnabled(project)) {
            log.debug("RAG not enabled for project={}", project.getId());
            return false;
        }
        
        // Check if base index is ready
        if (!isRagIndexReady(project)) {
            log.warn("Cannot ensure branch index - base RAG index not ready for project={}", project.getId());
            eventConsumer.accept(Map.of(
                "type", "warning",
                "message", "Base RAG index not ready"
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
        String baseBranch = getBaseBranch(project);
        
        // Same branch? Already indexed via main index
        if (targetBranch.equals(baseBranch)) {
            log.debug("Target branch {} is same as base branch {} - already indexed", targetBranch, baseBranch);
            return true;
        }
        
        // Check if branch already has indexed data (RagBranchIndex exists)
        // Note: We still proceed with diff check to ensure any new changes are indexed
        boolean branchIndexExists = isBranchIndexReady(project, targetBranch);
        log.info("Branch index status for project={}, branch={}: exists={}", 
                project.getId(), targetBranch, branchIndexExists);
        
        try {
            log.info("Fetching diff between base branch '{}' and target branch '{}' for project={}", 
                    baseBranch, targetBranch, project.getId());
            
            eventConsumer.accept(Map.of(
                "type", "status",
                "state", "branch_index",
                "message", String.format("Indexing branch '%s' (diff vs '%s')", targetBranch, baseBranch)
            ));
            
            // Fetch diff between base branch and target branch
            VcsClient vcsClient = vcsClientProvider.getClient(vcsConnection);
            String rawDiff = vcsClient.getBranchDiff(workspaceSlug, repoSlug, baseBranch, targetBranch);
            
            log.info("Branch diff result for project={}, branch={}: diffLength={}", 
                    project.getId(), targetBranch, rawDiff != null ? rawDiff.length() : 0);
            
            if (rawDiff == null || rawDiff.isEmpty()) {
                log.info("No diff between '{}' and '{}' - branch has same content as base, using main index", 
                        baseBranch, targetBranch);
                eventConsumer.accept(Map.of(
                    "type", "info",
                    "message", String.format("No changes between %s and %s - using main branch index", baseBranch, targetBranch)
                ));
                return true;
            }
            
            // Get latest commit hash on target branch
            String targetCommit = vcsClient.getLatestCommitHash(workspaceSlug, repoSlug, targetBranch);
            log.info("Target branch '{}' commit hash: {}", targetBranch, targetCommit);
            
            // Trigger incremental update for this branch
            log.info("Triggering incremental update for project={}, branch={}, commit={}, diffBytes={}", 
                    project.getId(), targetBranch, targetCommit, rawDiff.length());
            triggerIncrementalUpdate(project, targetBranch, targetCommit, rawDiff, eventConsumer);
            
            return true;
            
        } catch (Exception e) {
            log.error("Failed to index branch data for project={}, branch={}: {}",
                    project.getId(), targetBranch, e.getMessage(), e);
            eventConsumer.accept(Map.of(
                "type", "warning",
                "state", "branch_error",
                "message", "Failed to index branch: " + e.getMessage()
            ));
            return false;
        }
    }
    
    @Override
    public boolean deleteBranchIndex(
            Project project,
            String branchName,
            Consumer<Map<String, Object>> eventConsumer
    ) {
        if (!isRagEnabled(project)) {
            log.debug("RAG not enabled for project={}", project.getId());
            return false;
        }
        
        String baseBranch = getBaseBranch(project);
        if (branchName.equals(baseBranch)) {
            log.warn("Cannot delete main branch index for project={}", project.getId());
            eventConsumer.accept(Map.of(
                "type", "warning",
                "message", "Cannot delete main branch index"
            ));
            return false;
        }
        
        VcsRepoBinding vcsRepoBinding = project.getVcsRepoBinding();
        if (vcsRepoBinding == null) {
            log.error("Project has no VcsRepoBinding configured");
            return false;
        }
        
        String workspaceSlug = vcsRepoBinding.getExternalNamespace();
        String projectSlug = vcsRepoBinding.getExternalRepoSlug();
        
        try {
            log.info("Deleting branch index for project={}, branch={}", project.getId(), branchName);
            
            eventConsumer.accept(Map.of(
                "type", "status",
                "state", "branch_delete",
                "message", String.format("Deleting RAG index for branch '%s'", branchName)
            ));
            
            // Delete from RAG pipeline
            boolean success = ragPipelineClient.deleteBranch(workspaceSlug, projectSlug, branchName);
            
            if (success) {
                // Clean up database tracking
                ragBranchIndexRepository.deleteByProjectIdAndBranchName(project.getId(), branchName);
                
                log.info("Successfully deleted branch index for project={}, branch={}", project.getId(), branchName);
                eventConsumer.accept(Map.of(
                    "type", "success",
                    "message", String.format("Deleted RAG index for branch '%s'", branchName)
                ));
                return true;
            } else {
                log.warn("Failed to delete branch index from RAG pipeline for project={}, branch={}",
                        project.getId(), branchName);
                return false;
            }
            
        } catch (Exception e) {
            log.error("Failed to delete branch index for project={}, branch={}",
                    project.getId(), branchName, e);
            eventConsumer.accept(Map.of(
                "type", "error",
                "message", "Failed to delete branch index: " + e.getMessage()
            ));
            return false;
        }
    }
    
    @Override
    public Map<String, Object> cleanupStaleBranches(
            Project project,
            java.util.Set<String> activeBranches,
            Consumer<Map<String, Object>> eventConsumer
    ) {
        if (!isRagEnabled(project)) {
            return Map.of("status", "skipped", "reason", "rag_disabled");
        }
        
        VcsRepoBinding vcsRepoBinding = project.getVcsRepoBinding();
        if (vcsRepoBinding == null) {
            return Map.of("status", "error", "reason", "no_vcs_binding");
        }
        
        String workspaceSlug = vcsRepoBinding.getExternalNamespace();
        String projectSlug = vcsRepoBinding.getExternalRepoSlug();
        String baseBranch = getBaseBranch(project);
        
        try {
            // Get indexed branches
            List<String> indexedBranches = ragPipelineClient.getIndexedBranches(workspaceSlug, projectSlug);
            
            // Determine branches to keep: base branch + active branches
            Set<String> branchesToKeep = new HashSet<>(activeBranches);
            branchesToKeep.add(baseBranch);
            
            // Find stale branches (indexed but not active)
            List<String> staleBranches = indexedBranches.stream()
                    .filter(b -> !branchesToKeep.contains(b))
                    .toList();
            
            if (staleBranches.isEmpty()) {
                log.info("No stale branches to cleanup for project={}", project.getId());
                return Map.of(
                    "status", "success",
                    "deleted_branches", List.of(),
                    "total_deleted", 0
                );
            }
            
            log.info("Cleaning up {} stale branches for project={}: {}", 
                    staleBranches.size(), project.getId(), staleBranches);
            
            eventConsumer.accept(Map.of(
                "type", "status",
                "state", "cleanup",
                "message", String.format("Cleaning up %d stale branches", staleBranches.size())
            ));
            
            List<String> deletedBranches = new ArrayList<>();
            List<String> failedBranches = new ArrayList<>();
            
            for (String branch : staleBranches) {
                try {
                    boolean success = ragPipelineClient.deleteBranch(workspaceSlug, projectSlug, branch);
                    if (success) {
                        ragBranchIndexRepository.deleteByProjectIdAndBranchName(project.getId(), branch);
                        deletedBranches.add(branch);
                    } else {
                        failedBranches.add(branch);
                    }
                } catch (Exception e) {
                    log.warn("Failed to delete stale branch {} for project={}: {}", 
                            branch, project.getId(), e.getMessage());
                    failedBranches.add(branch);
                }
            }
            
            log.info("Cleanup complete for project={}: deleted={}, failed={}", 
                    project.getId(), deletedBranches.size(), failedBranches.size());
            
            eventConsumer.accept(Map.of(
                "type", "success",
                "message", String.format("Cleaned up %d stale branches", deletedBranches.size())
            ));
            
            return Map.of(
                "status", "success",
                "deleted_branches", deletedBranches,
                "failed_branches", failedBranches,
                "total_deleted", deletedBranches.size()
            );
            
        } catch (Exception e) {
            log.error("Failed to cleanup stale branches for project={}", project.getId(), e);
            return Map.of(
                "status", "error",
                "reason", e.getMessage()
            );
        }
    }
    
    @Override
    public boolean ensureRagIndexUpToDate(
            Project project,
            String targetBranch,
            Consumer<Map<String, Object>> eventConsumer
    ) {
        log.info("ensureRagIndexUpToDate called for project={}, targetBranch={}", project.getId(), targetBranch);
        
        if (!isRagEnabled(project)) {
            log.info("RAG not enabled for project={}", project.getId());
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
        log.info("Base branch for project={}: '{}'", project.getId(), baseBranch);
        
        try {
            VcsClient vcsClient = vcsClientProvider.getClient(vcsConnection);
            
            // Case 1: Target branch is the main branch - check/update main RAG index
            if (targetBranch.equals(baseBranch)) {
                log.info("Target branch '{}' equals base branch '{}' - updating main index only", targetBranch, baseBranch);
                return ensureMainIndexUpToDate(project, targetBranch, vcsClient, workspaceSlug, repoSlug, eventConsumer);
            }
            
            // Case 2: Different branch - ensure main index is ready, then ensure branch is indexed
            log.info("Target branch '{}' differs from base branch '{}' - will ensure branch index", targetBranch, baseBranch);
            
            // First ensure main index is up to date
            ensureMainIndexUpToDate(project, baseBranch, vcsClient, workspaceSlug, repoSlug, eventConsumer);
            
            // Then ensure branch data is indexed
            return ensureBranchIndexUpToDate(project, targetBranch, baseBranch, vcsClient, workspaceSlug, repoSlug, eventConsumer);
            
        } catch (Exception e) {
            log.error("Failed to ensure RAG index up-to-date for project={}, targetBranch={}",
                    project.getId(), targetBranch, e);
            eventConsumer.accept(Map.of(
                "type", "warning",
                "state", "rag_error",
                "message", "Failed to update RAG index: " + e.getMessage()
            ));
            return isRagIndexReady(project);
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
        
        // Fetch diff between indexed commit and current commit
        String rawDiff = vcsClient.getBranchDiff(workspaceSlug, repoSlug, indexedCommit, currentCommit);
        
        if (rawDiff == null || rawDiff.isEmpty()) {
            log.debug("No diff between {} and {} - index is up to date", indexedCommit, currentCommit);
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
     * Ensures the branch index is up-to-date with the current commit.
     * For non-main branches, this compares against the previously indexed commit.
     */
    private boolean ensureBranchIndexUpToDate(
            Project project,
            String targetBranch,
            String baseBranch,
            VcsClient vcsClient,
            String workspaceSlug,
            String repoSlug,
            Consumer<Map<String, Object>> eventConsumer
    ) throws IOException {
        log.info("ensureBranchIndexUpToDate called for project={}, targetBranch={}, baseBranch={}", 
                project.getId(), targetBranch, baseBranch);
        
        // Get current commit on target branch
        String currentCommit = vcsClient.getLatestCommitHash(workspaceSlug, repoSlug, targetBranch);
        log.info("Current commit on branch '{}': {}", targetBranch, currentCommit);
        
        // Check if we have branch index tracking
        Optional<RagBranchIndex> branchIndexOpt = ragBranchIndexRepository
                .findByProjectIdAndBranchName(project.getId(), targetBranch);
        
        if (branchIndexOpt.isEmpty()) {
            // No branch index exists - create it by getting full diff vs main
            log.info("No RagBranchIndex entry found for project={}, branch={} - will create with full diff vs {}", 
                    project.getId(), targetBranch, baseBranch);
            return ensureBranchIndexForPrTarget(project, targetBranch, eventConsumer);
        }
        
        RagBranchIndex branchIndex = branchIndexOpt.get();
        String indexedCommit = branchIndex.getCommitHash();
        log.info("Existing RagBranchIndex for project={}, branch={}: indexedCommit={}", 
                project.getId(), targetBranch, indexedCommit);
        
        // If commits match, index is up to date
        if (currentCommit.equals(indexedCommit)) {
            log.info("Branch index is up-to-date for project={}, branch={}, commit={}", 
                    project.getId(), targetBranch, currentCommit);
            return true;
        }
        
        log.info("Branch index outdated for project={}, branch={}: indexed={}, current={} - fetching incremental diff", 
                project.getId(), targetBranch, indexedCommit, currentCommit);
        
        // Fetch diff between indexed commit and current commit on this branch
        String rawDiff = vcsClient.getBranchDiff(workspaceSlug, repoSlug, indexedCommit, currentCommit);
        log.info("Incremental diff for branch '{}' ({}..{}): bytes={}", 
                targetBranch, indexedCommit.substring(0, Math.min(7, indexedCommit.length())), 
                currentCommit.substring(0, 7), rawDiff != null ? rawDiff.length() : 0);
        
        if (rawDiff == null || rawDiff.isEmpty()) {
            log.info("No diff between {} and {} - updating commit hash only", indexedCommit, currentCommit);
            // Update commit hash
            branchIndex.setCommitHash(currentCommit);
            branchIndex.setUpdatedAt(OffsetDateTime.now());
            ragBranchIndexRepository.save(branchIndex);
            return true;
        }
        
        eventConsumer.accept(Map.of(
            "type", "status",
            "state", "branch_update",
            "message", String.format("Updating branch %s index from %s to %s", 
                    targetBranch, indexedCommit.substring(0, Math.min(7, indexedCommit.length())), 
                    currentCommit.substring(0, 7))
        ));
        
        // Trigger incremental update for this branch
        log.info("Triggering incremental update for branch '{}' with diff of {} bytes", 
                targetBranch, rawDiff.length());
        triggerIncrementalUpdate(project, targetBranch, currentCommit, rawDiff, eventConsumer);
        
        return true;
    }
    
}
