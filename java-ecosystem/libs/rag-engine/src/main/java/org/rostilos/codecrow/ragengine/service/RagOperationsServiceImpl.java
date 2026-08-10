package org.rostilos.codecrow.ragengine.service;

import org.rostilos.codecrow.analysisapi.rag.RagOperationsService;
import org.rostilos.codecrow.core.model.analysis.AnalysisLockType;
import org.rostilos.codecrow.core.model.analysis.RagIndexStatus;
import org.rostilos.codecrow.core.model.job.Job;
import org.rostilos.codecrow.core.model.job.JobTriggerSource;
import org.rostilos.codecrow.core.model.project.Project;
import org.rostilos.codecrow.core.model.rag.RagBranchIndex;
import org.rostilos.codecrow.core.model.rag.RagBranchIndexGeneration;
import org.rostilos.codecrow.core.model.rag.RagBranchIndexGenerationStatus;
import org.rostilos.codecrow.core.model.rag.RagBranchIndexKind;
import org.rostilos.codecrow.core.model.vcs.VcsConnection;
import org.rostilos.codecrow.core.model.vcs.VcsRepoBinding;
import org.rostilos.codecrow.core.persistence.repository.rag.RagBranchIndexRepository;
import org.rostilos.codecrow.core.persistence.repository.rag.RagBranchIndexGenerationRepository;
import org.rostilos.codecrow.core.service.AnalysisJobService;
import org.rostilos.codecrow.analysisengine.service.AnalysisLockService;
import org.rostilos.codecrow.ragengine.client.RagPipelineClient;
import org.rostilos.codecrow.ragengine.branch.BranchIndexGenerationBuildService;
import org.rostilos.codecrow.vcsclient.VcsClient;
import org.rostilos.codecrow.vcsclient.VcsClientProvider;
import org.slf4j.Logger;
import org.slf4j.LoggerFactory;
import org.springframework.beans.factory.annotation.Value;
import org.springframework.stereotype.Service;
import org.springframework.beans.factory.annotation.Autowired;
import org.springframework.transaction.annotation.Transactional;

import java.io.IOException;
import java.time.OffsetDateTime;
import java.util.ArrayList;
import java.util.HashSet;
import java.util.LinkedHashSet;
import java.util.List;
import java.util.Map;
import java.util.Optional;
import java.util.Set;
import java.util.function.Consumer;

/**
 * Coordinates both legacy project collections and exact branch generations.
 * Existing projects retain the shared collection path until exact branch
 * indexing is configured; configured branches use immutable, branch-bound
 * generation targets selected through the registry.
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
    private final RagBranchIndexRegistryService branchIndexRegistryService;
    private final BranchIndexGenerationBuildService branchGenerationBuildService;

    @Autowired(required = false)
    private RagBranchIndexGenerationRepository branchGenerationRepository;

    @Value("${codecrow.rag.api.enabled:true}")
    private boolean ragApiEnabled;

    public RagOperationsServiceImpl(
            RagIndexTrackingService ragIndexTrackingService,
            IncrementalRagUpdateService incrementalRagUpdateService,
            AnalysisLockService analysisLockService,
            AnalysisJobService analysisJobService,
            RagBranchIndexRepository ragBranchIndexRepository,
            VcsClientProvider vcsClientProvider,
            RagPipelineClient ragPipelineClient) {
        this(ragIndexTrackingService, incrementalRagUpdateService,
                analysisLockService, analysisJobService,
                ragBranchIndexRepository, vcsClientProvider,
                ragPipelineClient, null, null);
    }

    public RagOperationsServiceImpl(
            RagIndexTrackingService ragIndexTrackingService,
            IncrementalRagUpdateService incrementalRagUpdateService,
            AnalysisLockService analysisLockService,
            AnalysisJobService analysisJobService,
            RagBranchIndexRepository ragBranchIndexRepository,
            VcsClientProvider vcsClientProvider,
            RagPipelineClient ragPipelineClient,
            RagBranchIndexRegistryService branchIndexRegistryService) {
        this(ragIndexTrackingService, incrementalRagUpdateService,
                analysisLockService, analysisJobService,
                ragBranchIndexRepository, vcsClientProvider,
                ragPipelineClient, branchIndexRegistryService, null);
    }

    @Autowired
    public RagOperationsServiceImpl(
            RagIndexTrackingService ragIndexTrackingService,
            IncrementalRagUpdateService incrementalRagUpdateService,
            AnalysisLockService analysisLockService,
            AnalysisJobService analysisJobService,
            RagBranchIndexRepository ragBranchIndexRepository,
            VcsClientProvider vcsClientProvider,
            RagPipelineClient ragPipelineClient,
            RagBranchIndexRegistryService branchIndexRegistryService,
            BranchIndexGenerationBuildService branchGenerationBuildService) {
        this.ragIndexTrackingService = ragIndexTrackingService;
        this.incrementalRagUpdateService = incrementalRagUpdateService;
        this.analysisLockService = analysisLockService;
        this.analysisJobService = analysisJobService;
        this.ragBranchIndexRepository = ragBranchIndexRepository;
        this.vcsClientProvider = vcsClientProvider;
        this.ragPipelineClient = ragPipelineClient;
        this.branchIndexRegistryService = branchIndexRegistryService;
        this.branchGenerationBuildService = branchGenerationBuildService;
    }

    @Override
    public boolean isRagPipelineHealthy() {
        return ragPipelineClient.isHealthy();
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
    public boolean deletePrFiles(Project project, int prNumber) {
        if (!ragApiEnabled) {
            log.debug("RAG disabled, skipping PR files deletion for PR #{}", prNumber);
            return true;
        }
        
        try {
            String workspace = project.getWorkspace().getName();
            String namespace = project.getNamespace();
            return ragPipelineClient.deletePrFiles(workspace, namespace, prNumber);
        } catch (Exception e) {
            log.warn("Failed to delete PR #{} files for project {}: {}", prNumber, project.getId(), e.getMessage());
            return false;
        }
    }

    @Override
    public boolean triggerIncrementalUpdate(
            Project project,
            String branchName,
            String commitHash,
            String rawDiff,
            Consumer<Map<String, Object>> eventConsumer) {
        Job job = null;
        log.info("triggerIncrementalUpdate called for project={}, branch={}, commit={}, diffLength={}",
                project.getId(), branchName, commitHash, rawDiff != null ? rawDiff.length() : 0);
        try {
            if (!incrementalRagUpdateService.shouldPerformIncrementalUpdate(project)) {
                log.info(
                        "Skipping RAG incremental update for project={}, branch={} - RAG not enabled or main branch not yet indexed",
                        project.getId(), branchName);
                eventConsumer.accept(Map.of(
                        "type", "info",
                        "message", "Skipping RAG update - main branch must be indexed first"));
                return false;
            }

            boolean exactGenerationMode = usesExactGenerations(project);
            RagBranchIndexKind exactGenerationKind = exactGenerationMode
                    ? indexKind(project, branchName) : null;
            boolean publishBranchAlias = exactGenerationKind == RagBranchIndexKind.PRIMARY
                    || exactGenerationKind == RagBranchIndexKind.DURABLE;
            boolean publishLegacyProjectAlias = exactGenerationKind
                    == RagBranchIndexKind.PRIMARY;
            RagBranchIndexGeneration initialSourceGeneration = exactGenerationMode
                    ? ragBranchIndexRepository
                            .findByProjectIdAndBranchName(project.getId(), branchName)
                            .map(RagBranchIndex::getActiveGeneration)
                            .orElse(null)
                    : null;
            boolean fullExactSnapshotRequired = exactGenerationMode
                    && initialSourceGeneration == null;

            String effectiveRawDiff = fullExactSnapshotRequired
                    ? ""
                    : resolveDiffFromCompletedCheckpoint(
                            project, branchName, commitHash, rawDiff);
            log.info("RAG checkpoint reconciliation complete; parsing effective diff...");

            // Parse the diff to find changed files
            IncrementalRagUpdateService.DiffResult diffResult =
                    incrementalRagUpdateService.parseDiffForRag(effectiveRawDiff);
            Set<String> addedFiles = diffResult.added();
            Set<String> modifiedFiles = diffResult.modified();
            Set<String> deletedFiles = diffResult.deleted();

            int addedOrModifiedSize = addedFiles.size() + modifiedFiles.size();

            log.info("Diff parsed: added={}, modified={}, deleted={}", addedFiles, modifiedFiles, deletedFiles);

            if (addedOrModifiedSize == 0 && deletedFiles.isEmpty()
                    && !exactGenerationMode) {
                log.info("Skipping RAG incremental update - no files changed in diff");
                return true;
            }

            log.info("RAG incremental update: {} files to add/update, {} files to delete",
                    addedOrModifiedSize, deletedFiles.size());

            job = analysisJobService.createRagIndexJob(project, false, JobTriggerSource.WEBHOOK);
            analysisJobService.info(job, "rag_init",
                    String.format(
                            "Starting incremental RAG update for branch '%s' (commit: %s) - %d files to update, %d to delete",
                            branchName, commitHash, addedOrModifiedSize, deletedFiles.size()));

            Optional<String> ragLockKey = analysisLockService.acquireLock(
                    project,
                    branchName,
                    AnalysisLockType.RAG_INDEXING,
                    commitHash,
                    null);

            if (ragLockKey.isEmpty()) {
                log.warn("RAG update already in progress for project={}, branch={}",
                        project.getId(), branchName);
                analysisJobService.warn(job, "rag_skip", "RAG update already in progress - skipping");
                analysisJobService.failJob(job, "RAG update already in progress");
                return false;
            }

            try {
                eventConsumer.accept(Map.of(
                        "type", "status",
                        "state", "rag_update",
                        "message",
                        "Updating RAG index with " + (addedOrModifiedSize + deletedFiles.size()) + " changed files"));

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

                RagBranchIndexGeneration sourceGeneration = null;
                RagBranchIndexRegistryService.BuildRegistration branchBuild = null;
                if (exactGenerationMode) {
                    sourceGeneration = initialSourceGeneration;
                    if (sourceGeneration != null) {
                        branchBuild = branchIndexRegistryService.registerBuild(
                                project,
                                branchName,
                                exactGenerationKind,
                                sourceGeneration.getRevision(),
                                commitHash,
                                sourceGeneration.getRepresentationFingerprint());
                        branchIndexRegistryService.startBuild(
                                branchBuild.operation().getId(),
                                job != null ? job.getId() : null);
                    }
                }

                Map<String, Object> result;
                try {
                    if (fullExactSnapshotRequired) {
                        var ragConfig = project.getConfiguration().ragConfig();
                        result = branchGenerationBuildService.build(
                                project,
                                vcsConnection,
                                workspaceSlug,
                                repoSlug,
                                branchName,
                                commitHash,
                                exactGenerationKind,
                                ragConfig.includePatterns(),
                                ragConfig.excludePatterns(),
                                job != null ? job.getId() : null);
                    } else if (exactGenerationMode) {
                        result = incrementalRagUpdateService.performIncrementalUpdate(
                                    project,
                                    vcsConnection,
                                    workspaceSlug,
                                    repoSlug,
                                    branchName,
                                    commitHash,
                                    addedFiles,
                                    modifiedFiles,
                                    deletedFiles,
                                    sourceGeneration.getRevision(),
                                    sourceGeneration.getCollectionName(),
                                    branchBuild.generation().getCollectionName(),
                                    false,
                                    false);
                    } else {
                        result = incrementalRagUpdateService.performIncrementalUpdate(
                                    project,
                                    vcsConnection,
                                    workspaceSlug,
                                    repoSlug,
                                    branchName,
                                    commitHash,
                                    addedFiles,
                                    modifiedFiles,
                                    deletedFiles);
                    }
                    if (exactGenerationMode && !fullExactSnapshotRequired) {
                        Object digest = result.get("generation_manifest_sha256");
                        if (!(digest instanceof String manifestDigest)
                                || manifestDigest.isBlank()) {
                            throw new IllegalStateException(
                                    "Advanced RAG generation has no manifest digest");
                        }
                        RagBranchIndexGeneration published = branchIndexRegistryService.publish(
                                branchBuild.operation().getId(),
                                manifestDigest,
                                ((Number) result.getOrDefault("document_count", 0)).intValue(),
                                ((Number) result.getOrDefault("chunk_count", 0)).intValue());
                        publishReadableAliasesIfActive(
                                project, branchName, commitHash,
                                branchBuild.generation().getCollectionName(),
                                published, publishBranchAlias,
                                publishLegacyProjectAlias);
                    }
                } catch (Exception generationFailure) {
                    if (branchBuild != null) {
                        branchIndexRegistryService.fail(
                                branchBuild.operation().getId(),
                                generationFailure.getMessage() != null
                                        ? generationFailure.getMessage()
                                        : generationFailure.getClass().getSimpleName());
                    }
                    throw generationFailure;
                }

                int filesUpdated = (Integer) result.getOrDefault("updatedFiles", 0);
                int filesDeleted = (Integer) result.getOrDefault("deletedFiles", 0);
                int filesSkipped = (Integer) result.getOrDefault("skippedFiles", 0);
                Integer newlyAddedFilesCount = (Integer) result.get("addedFilesCount");

                Integer chunkCount = null;
                if (result.get("chunk_count") != null) {
                    chunkCount = ((Number) result.get("chunk_count")).intValue();
                }

                ragIndexTrackingService.markUpdatingCompleted(
                        project,
                        branchName,
                        commitHash,
                        newlyAddedFilesCount != null ? newlyAddedFilesCount : 0,
                        filesDeleted,
                        chunkCount,
                        branchName.equals(getBaseBranch(project)));

                // Track branch index for deleted files
                trackBranchIndex(project, branchName, commitHash, deletedFiles);

                eventConsumer.accept(Map.of(
                        "type", "status",
                        "state", "rag_complete",
                        "message",
                        String.format(
                                "RAG index updated: %d files updated, %d deleted, %d non-text files skipped",
                                filesUpdated, filesDeleted, filesSkipped)));

                log.info("RAG incremental update completed for project={}: {} files updated, {} deleted, "
                                + "{} non-text files skipped",
                        project.getId(), filesUpdated, filesDeleted, filesSkipped);
                analysisJobService.info(job, "rag_complete",
                        String.format(
                                "RAG incremental update completed: %d files updated, %d deleted, "
                                        + "%d non-text files skipped",
                                filesUpdated, filesDeleted, filesSkipped));
                analysisJobService.completeJob(job, null);
                return true;

            } catch (Exception e) {
                // Use markIncrementalUpdateFailed (keeps status INDEXED, increments failure counter)
                // NOT markIndexingFailed which would set status to FAILED and permanently block
                // all future incremental updates even though the base index is still valid.
                ragIndexTrackingService.markIncrementalUpdateFailed(project, e.getMessage());
                log.error("RAG incremental update failed", e);
                if (job != null) {
                    analysisJobService.error(job, "rag_error", "RAG incremental update failed: " + e.getMessage());
                    analysisJobService.failJob(job, "RAG incremental update failed: " + e.getMessage());
                }
                eventConsumer.accept(Map.of(
                        "type", "warning",
                        "state", "rag_error",
                        "message", "RAG incremental update failed: " + e.getMessage()));
                return false;
            } finally {
                analysisLockService.releaseLock(ragLockKey.get());
            }
        } catch (Exception e) {
            log.warn("RAG incremental update failed (non-critical): {}", e.getMessage());
            if (job != null) {
                analysisJobService.error(job, "rag_error",
                        "RAG incremental update failed (non-critical): " + e.getMessage());
                analysisJobService.failJob(job, e.getMessage());
            }
            eventConsumer.accept(Map.of(
                    "type", "warning",
                    "state", "rag_error",
                    "message", "RAG incremental update failed: " + e.getMessage()));
            return false;
        }
    }

    private void publishReadableAliasesIfActive(
            Project project,
            String branch,
            String revision,
            String collectionTarget,
            RagBranchIndexGeneration published,
            boolean publishBranchAlias,
            boolean publishLegacyProjectAlias) {
        if (published == null
                || published.getStatus() != RagBranchIndexGenerationStatus.ACTIVE
                || !publishBranchAlias) {
            return;
        }
        try {
            ragPipelineClient.publishGenerationAliases(
                    project.getWorkspace().getName(), project.getNamespace(),
                    branch, revision, collectionTarget,
                    true, publishLegacyProjectAlias);
        } catch (IOException aliasFailure) {
            log.warn("Readable alias publication failed for active RAG generation {}: {}",
                    published.getId(), aliasFailure.getMessage());
        }
    }

    /**
     * Rebuilds the effective range from the last completed RAG checkpoint.
     * The caller's branch-analysis diff is used only when no completed
     * checkpoint exists yet for this branch.
     */
    private String resolveDiffFromCompletedCheckpoint(
            Project project,
            String branchName,
            String commitHash,
            String suppliedDiff) throws IOException {
        if (commitHash == null || commitHash.isBlank()) {
            throw new IOException("Cannot incrementally update RAG without a target commit hash");
        }

        String baseBranch = getBaseBranch(project);
        Optional<String> completedCheckpoint;
        if (branchName.equals(baseBranch)) {
            completedCheckpoint = ragIndexTrackingService.getIndexStatus(project)
                    .map(RagIndexStatus::getIndexedCommitHash);
        } else {
            completedCheckpoint = ragBranchIndexRepository
                    .findByProjectIdAndBranchName(project.getId(), branchName)
                    .map(RagBranchIndex::getCommitHash);
        }

        String checkpoint = completedCheckpoint
                .filter(value -> !value.isBlank())
                .orElse(null);
        if (checkpoint == null) {
            log.info("No completed RAG checkpoint for branch {}; using supplied initial diff",
                    branchName);
            return suppliedDiff;
        }
        if (checkpoint.equals(commitHash)) {
            log.info("RAG checkpoint already represents branch {} commit {}", branchName, commitHash);
            return "";
        }

        VcsRepoBinding vcsRepoBinding = project.getVcsRepoBinding();
        if (vcsRepoBinding == null) {
            throw new IOException("Project has no VcsRepoBinding configured");
        }
        VcsConnection vcsConnection = vcsRepoBinding.getVcsConnection();
        VcsClient vcsClient = vcsClientProvider.getClient(vcsConnection);
        String workspaceSlug = vcsRepoBinding.getExternalNamespace();
        String repoSlug = vcsRepoBinding.getExternalRepoSlug();

        log.info("Fetching catch-up RAG diff for branch {} from completed checkpoint {} to {}",
                branchName, checkpoint, commitHash);
        String catchUpDiff = vcsClient.getBranchDiff(
                workspaceSlug, repoSlug, checkpoint, commitHash);
        return catchUpDiff != null ? catchUpDiff : "";
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
            Consumer<Map<String, Object>> eventConsumer) {
        // With single-collection architecture, we just do incremental update
        // No separate collection needed - branch data goes into shared collection
        triggerIncrementalUpdate(project, branchName, branchCommit, rawDiff, eventConsumer);
    }

    @Override
    public boolean updateBranchIndex(
            Project project,
            String targetBranch,
            Consumer<Map<String, Object>> eventConsumer) {
        if (!isRagEnabled(project)) {
            log.debug("RAG not enabled for project={}", project.getId());
            return false;
        }

        if (!isRagIndexReady(project)) {
            log.warn("Cannot update branch index - base RAG index not ready for project={}", project.getId());
            return false;
        }

        if (!targetBranch.equals(getBaseBranch(project))
                && !shouldHaveBranchIndex(project, targetBranch)) {
            log.info("Skipping branch index update for non-retained branch: project={}, branch={}",
                    project.getId(), targetBranch);
            eventConsumer.accept(Map.of(
                    "type", "info",
                    "state", "rag_skipped",
                    "message", "Branch is not configured as a retained RAG branch"));
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

            String targetCommit = vcsClient.getLatestCommitHash(workspaceSlug, repoSlug, targetBranch);
            Optional<RagBranchIndex> completedBranchIndex = ragBranchIndexRepository
                    .findByProjectIdAndBranchName(project.getId(), targetBranch)
                    .filter(index -> index.getCommitHash() != null && !index.getCommitHash().isBlank());

            if (completedBranchIndex.isPresent()) {
                String checkpoint = completedBranchIndex.get().getCommitHash();
                if (checkpoint.equals(targetCommit)) {
                    log.info("Branch index already represents project={}, branch={}, commit={}",
                            project.getId(), targetBranch, targetCommit);
                    return true;
                }

                log.info("Updating branch index from completed checkpoint: project={}, branch={}, {}..{}",
                        project.getId(), targetBranch, checkpoint, targetCommit);
                // triggerIncrementalUpdate resolves the exact checkpoint range. Passing an
                // empty supplied diff avoids the former redundant base-to-target compare.
                return triggerIncrementalUpdate(
                        project, targetBranch, targetCommit, "", eventConsumer);
            }

            log.info("Seeding legacy branch index for project={}, branch={} (diff vs {})",
                    project.getId(), targetBranch, baseBranch);

            eventConsumer.accept(Map.of(
                    "type", "status",
                    "state", "branch_index",
                    "message", String.format("Calculating diff between '%s' and '%s'", baseBranch, targetBranch)));

            // Compatibility path for a branch that has no checkpoint yet. The exact
            // generation builder replaces this with a complete verified snapshot.
            String rawDiff = vcsClient.getBranchDiff(workspaceSlug, repoSlug, baseBranch, targetBranch);

            if (rawDiff == null || rawDiff.isEmpty()) {
                log.info("No diff between {} and {} - branch has same content as base", baseBranch, targetBranch);
                eventConsumer.accept(Map.of(
                        "type", "info",
                        "message", String.format("Branch '%s' has same content as '%s'", targetBranch, baseBranch)));
                if (usesExactGenerations(project)) {
                    // No completed checkpoint means there is still no exact
                    // target-branch generation. An empty tree delta must seed
                    // the complete revision rather than report a false success.
                    return triggerIncrementalUpdate(
                            project, targetBranch, targetCommit, "", eventConsumer);
                }
                return true;
            }

            log.info("Branch diff found: {} bytes, triggering incremental update for branch={}, commit={}",
                    rawDiff.length(), targetBranch, targetCommit);

            // Trigger incremental update with full branch diff
            return triggerIncrementalUpdate(
                    project, targetBranch, targetCommit, rawDiff, eventConsumer);

        } catch (Exception e) {
            log.error("Failed to update branch index for project={}, branch={}",
                    project.getId(), targetBranch, e);
            eventConsumer.accept(Map.of(
                    "type", "error",
                    "message", "Failed to update branch index: " + e.getMessage()));
            return false;
        }
    }

    @Override
    public boolean ensureBranchIndexForPrTarget(
            Project project,
            String targetBranch,
            Consumer<Map<String, Object>> eventConsumer) {
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
                    "message", "Base RAG index not ready"));
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

        // Exact-generation mode seeds an immutable snapshot of the target
        // revision. It must not first compute the potentially enormous
        // primary-to-target diff that motivated multi-branch indexing.
        if (usesExactGenerations(project)) {
            if (!shouldHaveBranchIndex(project, targetBranch)
                    && !shouldCreateTransientBranchIndex(project, targetBranch)) {
                return false;
            }
            try {
                VcsClient vcsClient = vcsClientProvider.getClient(vcsConnection);
                String targetCommit = vcsClient.getLatestCommitHash(
                        workspaceSlug, repoSlug, targetBranch);
                return triggerIncrementalUpdate(
                        project, targetBranch, targetCommit, "", eventConsumer);
            } catch (Exception failure) {
                log.warn("Failed to seed exact branch generation for project={}, branch={}: {}",
                        project.getId(), targetBranch, failure.getMessage());
                return false;
            }
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
                    "message", String.format("Indexing branch '%s' (diff vs '%s')", targetBranch, baseBranch)));

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
                        "message", String.format("No changes between %s and %s - using main branch index", baseBranch,
                                targetBranch)));
                return true;
            }

            // Get latest commit hash on target branch
            String targetCommit = vcsClient.getLatestCommitHash(workspaceSlug, repoSlug, targetBranch);
            log.info("Target branch '{}' commit hash: {}", targetBranch, targetCommit);

            // Trigger incremental update for this branch
            log.info("Triggering incremental update for project={}, branch={}, commit={}, diffBytes={}",
                    project.getId(), targetBranch, targetCommit, rawDiff.length());
            return triggerIncrementalUpdate(
                    project, targetBranch, targetCommit, rawDiff, eventConsumer);

        } catch (Exception e) {
            log.error("Failed to index branch data for project={}, branch={}: {}",
                    project.getId(), targetBranch, e.getMessage(), e);
            eventConsumer.accept(Map.of(
                    "type", "warning",
                    "state", "branch_error",
                    "message", "Failed to index branch: " + e.getMessage()));
            return false;
        }
    }

    @Override
    public boolean deleteBranchIndex(
            Project project,
            String branchName,
            Consumer<Map<String, Object>> eventConsumer) {
        if (!isRagEnabled(project)) {
            log.debug("RAG not enabled for project={}", project.getId());
            return false;
        }

        String baseBranch = getBaseBranch(project);
        if (branchName.equals(baseBranch)) {
            log.warn("Cannot delete main branch index for project={}", project.getId());
            eventConsumer.accept(Map.of(
                    "type", "warning",
                    "message", "Cannot delete main branch index"));
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
                    "message", String.format("Deleting RAG index for branch '%s'", branchName)));

            boolean success;
            Optional<RagBranchIndex> trackedIndex = ragBranchIndexRepository
                    .findByProjectIdAndBranchName(project.getId(), branchName);
            List<RagBranchIndexGeneration> generations = trackedIndex.isPresent()
                    && branchGenerationRepository != null
                    ? branchGenerationRepository.findByBranchIndexIdOrderByCreatedAtDesc(
                            trackedIndex.get().getId())
                    : List.of();
            if (!generations.isEmpty()) {
                success = true;
                for (RagBranchIndexGeneration generation : generations) {
                    success &= ragPipelineClient.deleteBranch(
                            project.getWorkspace().getName(), project.getNamespace(),
                            branchName, generation.getCollectionName());
                }
            } else {
                // Backward-compatible cleanup for the legacy shared collection.
                success = ragPipelineClient.deleteBranch(
                        workspaceSlug, projectSlug, branchName);
            }

            if (success) {
                // Clean up database tracking
                ragBranchIndexRepository.deleteByProjectIdAndBranchName(project.getId(), branchName);

                log.info("Successfully deleted branch index for project={}, branch={}", project.getId(), branchName);
                eventConsumer.accept(Map.of(
                        "type", "success",
                        "message", String.format("Deleted RAG index for branch '%s'", branchName)));
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
                    "message", "Failed to delete branch index: " + e.getMessage()));
            return false;
        }
    }

    @Override
    public Map<String, Object> cleanupStaleBranches(
            Project project,
            java.util.Set<String> activeBranches,
            Consumer<Map<String, Object>> eventConsumer) {
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
            // The durable registry is authoritative for exact-generation
            // branches. Merge it with legacy shared-collection discovery so
            // stale cleanup remains compatible with both storage models.
            Set<String> indexedBranches = new LinkedHashSet<>(
                    ragBranchIndexRepository.findBranchNamesByProjectId(project.getId()));
            indexedBranches.addAll(
                    ragPipelineClient.getIndexedBranches(workspaceSlug, projectSlug));

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
                        "total_deleted", 0);
            }

            log.info("Cleaning up {} stale branches for project={}: {}",
                    staleBranches.size(), project.getId(), staleBranches);

            eventConsumer.accept(Map.of(
                    "type", "status",
                    "state", "cleanup",
                    "message", String.format("Cleaning up %d stale branches", staleBranches.size())));

            List<String> deletedBranches = new ArrayList<>();
            List<String> failedBranches = new ArrayList<>();

            for (String branch : staleBranches) {
                try {
                    boolean success = deleteBranchIndex(project, branch, eventConsumer);
                    if (success) {
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
                    "message", String.format("Cleaned up %d stale branches", deletedBranches.size())));

            return Map.of(
                    "status", "success",
                    "deleted_branches", deletedBranches,
                    "failed_branches", failedBranches,
                    "total_deleted", deletedBranches.size());

        } catch (Exception e) {
            log.error("Failed to cleanup stale branches for project={}", project.getId(), e);
            return Map.of(
                    "status", "error",
                    "reason", e.getMessage());
        }
    }

    @Override
    public boolean ensureRagIndexUpToDate(
            Project project,
            String targetBranch,
            Consumer<Map<String, Object>> eventConsumer) {
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
                log.info("Target branch '{}' equals base branch '{}' - updating main index only", targetBranch,
                        baseBranch);
                return ensureMainIndexUpToDate(project, targetBranch, vcsClient, workspaceSlug, repoSlug,
                        eventConsumer);
            }

            if (!shouldHaveBranchIndex(project, targetBranch)
                    && !shouldCreateTransientBranchIndex(project, targetBranch)) {
                log.info("Skipping RAG preparation for non-indexed PR target: project={}, branch={}",
                        project.getId(), targetBranch);
                eventConsumer.accept(Map.of(
                        "type", "info",
                        "state", "rag_skipped",
                        "message", "PR target is not configured for retained or temporary RAG indexing"));
                return false;
            }

            // Case 2: Different branch - ensure main index is ready, then ensure branch is
            // indexed
            log.info("Target branch '{}' differs from base branch '{}' - will ensure branch index", targetBranch,
                    baseBranch);

            // First ensure main index is up to date
            ensureMainIndexUpToDate(project, baseBranch, vcsClient, workspaceSlug, repoSlug, eventConsumer);

            // Then ensure branch data is indexed
            return ensureBranchIndexUpToDate(project, targetBranch, baseBranch, vcsClient, workspaceSlug, repoSlug,
                    eventConsumer);

        } catch (Exception e) {
            log.error("Failed to ensure RAG index up-to-date for project={}, targetBranch={}",
                    project.getId(), targetBranch, e);
            eventConsumer.accept(Map.of(
                    "type", "warning",
                    "state", "rag_error",
                    "message", "Failed to update RAG index: " + e.getMessage()));
            return isRagIndexReady(project);
        }
    }

    /**
     * Ensures the main RAG index is up-to-date with the current commit on the
     * branch.
     */
    private boolean ensureMainIndexUpToDate(
            Project project,
            String branchName,
            VcsClient vcsClient,
            String workspaceSlug,
            String repoSlug,
            Consumer<Map<String, Object>> eventConsumer) throws IOException {
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

        if (usesExactGenerations(project)
                && ragBranchIndexRepository
                        .findByProjectIdAndBranchName(project.getId(), branchName)
                        .map(RagBranchIndex::getActiveGeneration)
                        .isEmpty()) {
            log.info("Creating first exact primary generation for project={}, branch={}, commit={}",
                    project.getId(), branchName, currentCommit);
            return triggerIncrementalUpdate(
                    project, branchName, currentCommit, "", eventConsumer);
        }

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
            ragIndexTrackingService.markUpdatingCompleted(project, branchName, currentCommit, 0, 0, null);
            return true;
        }

        eventConsumer.accept(Map.of(
                "type", "status",
                "state", "rag_update",
                "message", String.format("Updating RAG index from %s to %s",
                        indexedCommit.substring(0, 7), currentCommit.substring(0, 7))));

        // Trigger incremental update
        return triggerIncrementalUpdate(
                project, branchName, currentCommit, rawDiff, eventConsumer);
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
            Consumer<Map<String, Object>> eventConsumer) throws IOException {
        log.info("ensureBranchIndexUpToDate called for project={}, targetBranch={}, baseBranch={}",
                project.getId(), targetBranch, baseBranch);

        // Get current commit on target branch
        String currentCommit = vcsClient.getLatestCommitHash(workspaceSlug, repoSlug, targetBranch);
        log.info("Current commit on branch '{}': {}", targetBranch, currentCommit);

        // Check if we have branch index tracking
        Optional<RagBranchIndex> branchIndexOpt = ragBranchIndexRepository
                .findByProjectIdAndBranchName(project.getId(), targetBranch);

        if (branchIndexOpt.isEmpty()) {
            // The exact path seeds from a complete target snapshot; the legacy
            // implementation below retains the former base-diff behavior.
            if (usesExactGenerations(project)) {
                return triggerIncrementalUpdate(
                        project, targetBranch, currentCommit, "", eventConsumer);
            }
            log.info("No RagBranchIndex entry found for project={}, branch={} - will create with full diff vs {}",
                    project.getId(), targetBranch, baseBranch);
            return ensureBranchIndexForPrTarget(project, targetBranch, eventConsumer);
        }

        RagBranchIndex branchIndex = branchIndexOpt.get();
        String indexedCommit = branchIndex.getCommitHash();
        log.info("Existing RagBranchIndex for project={}, branch={}: indexedCommit={}",
                project.getId(), targetBranch, indexedCommit);

        if (usesExactGenerations(project) && branchIndex.getActiveGeneration() == null) {
            return triggerIncrementalUpdate(
                    project, targetBranch, currentCommit, "", eventConsumer);
        }

        // If commits match, index is up to date
        if (currentCommit.equals(indexedCommit)) {
            log.info("Branch index is up-to-date for project={}, branch={}, commit={}",
                    project.getId(), targetBranch, currentCommit);
            return true;
        }

        // Branch index exists but commit changed - do INCREMENTAL update (only new
        // changes)
        // Full diff vs main is only done on INITIAL indexing (when RagBranchIndex
        // doesn't exist)
        log.info("Branch index outdated for project={}, branch={}: indexed={}, current={} - fetching incremental diff",
                project.getId(), targetBranch, indexedCommit, currentCommit);

        // Fetch diff between last indexed commit and current commit (incremental)
        String rawDiff = vcsClient.getBranchDiff(workspaceSlug, repoSlug, indexedCommit, currentCommit);
        log.info("Incremental diff for branch '{}' ({} -> {}): bytes={}",
                targetBranch, indexedCommit.substring(0, 7), currentCommit.substring(0, 7),
                rawDiff != null ? rawDiff.length() : 0);

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
                "message",
                String.format("Updating branch %s index (incremental: %d bytes)", targetBranch, rawDiff.length())));

        // Trigger incremental update for this branch
        log.info("Triggering incremental branch update for '{}' with {} bytes diff",
                targetBranch, rawDiff.length());
        return triggerIncrementalUpdate(
                project, targetBranch, currentCommit, rawDiff, eventConsumer);
    }

    private boolean usesExactGenerations(Project project) {
        return branchIndexRegistryService != null
                && branchGenerationBuildService != null
                && project != null
                && project.getConfiguration() != null
                && project.getConfiguration().ragConfig() != null
                && project.getConfiguration().ragConfig().isMultiBranchEnabled();
    }

    private RagBranchIndexKind indexKind(Project project, String branchName) {
        if (branchName.equals(getBaseBranch(project))) {
            return RagBranchIndexKind.PRIMARY;
        }
        return shouldHaveBranchIndex(project, branchName)
                ? RagBranchIndexKind.DURABLE
                : RagBranchIndexKind.TRANSIENT;
    }

}
