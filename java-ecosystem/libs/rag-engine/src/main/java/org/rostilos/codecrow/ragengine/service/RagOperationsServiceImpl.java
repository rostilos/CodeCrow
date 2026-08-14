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
import org.rostilos.codecrow.ragengine.branch.BranchIndexBuildAdmissionService;
import org.rostilos.codecrow.ragengine.branch.LegacyRagJobLeaseService;
import org.rostilos.codecrow.ragengine.branch.LegacyRagUpdateCompletionService;
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
import java.util.Comparator;
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
    private final BranchIndexBuildAdmissionService branchIndexBuildAdmissionService;
    private final LegacyRagJobLeaseService legacyRagJobLeaseService;
    private final LegacyRagUpdateCompletionService legacyRagUpdateCompletionService;

    @Autowired(required = false)
    private RagBranchIndexGenerationRepository branchGenerationRepository;

    @Value("${codecrow.rag.api.enabled:true}")
    private boolean ragApiEnabled;

    @Value("${analysis.lock.rag.timeout.minutes:360}")
    private int legacyRagLockLeaseMinutes = 360;

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
                ragPipelineClient, null, null, null, null, null);
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
                ragPipelineClient, branchIndexRegistryService, null, null, null, null);
    }

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
        this(ragIndexTrackingService, incrementalRagUpdateService,
                analysisLockService, analysisJobService,
                ragBranchIndexRepository, vcsClientProvider,
                ragPipelineClient, branchIndexRegistryService,
                branchGenerationBuildService, null, null, null);
    }

    public RagOperationsServiceImpl(
            RagIndexTrackingService ragIndexTrackingService,
            IncrementalRagUpdateService incrementalRagUpdateService,
            AnalysisLockService analysisLockService,
            AnalysisJobService analysisJobService,
            RagBranchIndexRepository ragBranchIndexRepository,
            VcsClientProvider vcsClientProvider,
            RagPipelineClient ragPipelineClient,
            RagBranchIndexRegistryService branchIndexRegistryService,
            BranchIndexGenerationBuildService branchGenerationBuildService,
            BranchIndexBuildAdmissionService branchIndexBuildAdmissionService) {
        this(ragIndexTrackingService, incrementalRagUpdateService,
                analysisLockService, analysisJobService,
                ragBranchIndexRepository, vcsClientProvider,
                ragPipelineClient, branchIndexRegistryService,
                branchGenerationBuildService, branchIndexBuildAdmissionService,
                null, null);
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
            BranchIndexGenerationBuildService branchGenerationBuildService,
            BranchIndexBuildAdmissionService branchIndexBuildAdmissionService,
            LegacyRagJobLeaseService legacyRagJobLeaseService,
            LegacyRagUpdateCompletionService legacyRagUpdateCompletionService) {
        this.ragIndexTrackingService = ragIndexTrackingService;
        this.incrementalRagUpdateService = incrementalRagUpdateService;
        this.analysisLockService = analysisLockService;
        this.analysisJobService = analysisJobService;
        this.ragBranchIndexRepository = ragBranchIndexRepository;
        this.vcsClientProvider = vcsClientProvider;
        this.ragPipelineClient = ragPipelineClient;
        this.branchIndexRegistryService = branchIndexRegistryService;
        this.branchGenerationBuildService = branchGenerationBuildService;
        this.branchIndexBuildAdmissionService = branchIndexBuildAdmissionService;
        this.legacyRagJobLeaseService = legacyRagJobLeaseService;
        this.legacyRagUpdateCompletionService = legacyRagUpdateCompletionService;
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
        if (usesExactGenerations(project)) {
            String primaryBranch = getBaseBranch(project);
            boolean primaryRegistered = ragBranchIndexRepository
                    .existsByProjectIdAndBranchName(project.getId(), primaryBranch);
            if (primaryRegistered) {
                if (ragBranchIndexRepository.markAccessedIfUnclaimed(
                        project.getId(), primaryBranch, OffsetDateTime.now()) == 0) {
                    return false;
                }
                return ragBranchIndexRepository.findActiveGenerationCoordinates(
                        project.getId(), primaryBranch).isPresent();
            }
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
            Set<String> collectionTargets = new LinkedHashSet<>();
            if (branchGenerationRepository != null) {
                List<String> registeredTargets = branchGenerationRepository
                        .findCollectionNamesByProjectIdAndStatusIn(
                                project.getId(),
                                List.of(
                                        RagBranchIndexGenerationStatus.ACTIVE,
                                        RagBranchIndexGenerationStatus.SUPERSEDED));
                if (registeredTargets != null) {
                    registeredTargets.stream()
                            .filter(target -> target != null && !target.isBlank())
                            .map(String::trim)
                            .forEach(collectionTargets::add);
                }
            }

            if (collectionTargets.isEmpty()) {
                // Legacy projects have no physical-generation registry. Their
                // project alias remains the only cleanup target.
                RagPipelineClient.PrFilesDeletionOutcome outcome;
                try {
                    outcome = ragPipelineClient.deletePrFilesWithOutcome(
                            workspace, namespace, prNumber, null);
                } catch (RuntimeException unexpectedFailure) {
                    log.warn("Failed to delete PR #{} files for project={} target=legacy-alias: "
                                    + "status=unexpected detail={}",
                            prNumber, project.getId(), unexpectedFailure.getMessage());
                    return false;
                }
                if (!outcome.successful()) {
                    logPrCleanupFailure(project, prNumber, outcome);
                }
                return outcome.successful();
            }

            boolean allTargetsCleaned = true;
            for (String collectionTarget : collectionTargets) {
                RagPipelineClient.PrFilesDeletionOutcome outcome;
                try {
                    outcome = ragPipelineClient.deletePrFilesWithOutcome(
                            workspace, namespace, prNumber, collectionTarget);
                } catch (RuntimeException unexpectedFailure) {
                    log.warn("Failed to delete PR #{} files for project={} target={}: "
                                    + "status=unexpected detail={}",
                            prNumber, project.getId(), collectionTarget,
                            unexpectedFailure.getMessage());
                    return false;
                }
                if (!outcome.successful()) {
                    allTargetsCleaned = false;
                    logPrCleanupFailure(project, prNumber, outcome);
                    if (outcome.shouldStopRemainingTargets()) {
                        break;
                    }
                }
            }
            return allTargetsCleaned;
        } catch (Exception e) {
            log.warn("Failed to delete PR #{} files for project {}: {}", prNumber, project.getId(), e.getMessage());
            return false;
        }
    }

    private static void logPrCleanupFailure(
            Project project,
            int prNumber,
            RagPipelineClient.PrFilesDeletionOutcome outcome) {
        log.warn("Failed to delete PR #{} files for project={} target={}: status={} detail={}",
                prNumber,
                project.getId(),
                outcome.targetLabel(),
                outcome.statusCode() != null ? outcome.statusCode() : outcome.failure(),
                outcome.detail());
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
            boolean exactGenerationMode = usesExactGenerations(project);
            boolean normalIncrementalReady =
                    incrementalRagUpdateService.shouldPerformIncrementalUpdate(project);
            boolean exactRecoveryCandidate = exactGenerationMode
                    && isRagEnabled(project)
                    && ragBranchIndexRepository.existsByProjectIdAndBranchName(
                            project.getId(), branchName);
            if (!normalIncrementalReady && !exactRecoveryCandidate) {
                log.info(
                        "Skipping RAG incremental update for project={}, branch={} - RAG not enabled or main branch not yet indexed",
                        project.getId(), branchName);
                emitEvent(eventConsumer, Map.of(
                        "type", "info",
                        "message", "Skipping RAG update - main branch must be indexed first"));
                return false;
            }

            boolean tracksProjectStatus = branchName.equals(getBaseBranch(project));
            RagBranchIndexKind exactGenerationKind = exactGenerationMode
                    ? indexKind(project, branchName) : null;
            Set<String> addedFiles = Set.of();
            Set<String> modifiedFiles = Set.of();
            Set<String> deletedFiles = Set.of();
            int addedOrModifiedSize = 0;
            boolean checkpointOnlyAdvance = false;
            boolean unrecognizedLegacyDiff = false;

            // Legacy collections reconcile from their own completed checkpoint.
            // A same-revision request is a true no-op. A newer revision with an
            // empty range must still own a durable job and advance its checkpoint
            // so later pushes do not repeatedly start from stale state.
            if (!exactGenerationMode) {
                LegacyDiffResolution diffResolution = resolveDiffFromCompletedCheckpoint(
                        project, branchName, commitHash, rawDiff);
                if (diffResolution.alreadyCurrent()) {
                    String message = String.format(
                            "RAG index for branch '%s' already represents commit %s",
                            branchName, commitHash);
                    log.info(message);
                    emitEvent(eventConsumer, Map.of(
                            "type", "info",
                            "state", "rag_current",
                            "message", message));
                    return true;
                }

                String effectiveRawDiff = diffResolution.rawDiff();
                log.info("RAG checkpoint reconciliation complete; parsing effective diff...");
                IncrementalRagUpdateService.DiffResult diffResult =
                        incrementalRagUpdateService.parseDiffForRag(effectiveRawDiff);
                addedFiles = diffResult.added();
                modifiedFiles = diffResult.modified();
                deletedFiles = diffResult.deleted();
                addedOrModifiedSize = addedFiles.size() + modifiedFiles.size();

                log.info("Diff parsed: added={}, modified={}, deleted={}",
                        addedFiles, modifiedFiles, deletedFiles);
                if (addedOrModifiedSize == 0 && deletedFiles.isEmpty()) {
                    checkpointOnlyAdvance = effectiveRawDiff == null
                            || effectiveRawDiff.isBlank();
                    unrecognizedLegacyDiff = !checkpointOnlyAdvance;
                    log.info(checkpointOnlyAdvance
                                    ? "No files changed; a durable RAG job will advance the checkpoint"
                                    : "Non-empty RAG diff did not contain recognizable file changes");
                } else {
                    log.info("RAG incremental update: {} files to add/update, {} files to delete",
                            addedOrModifiedSize, deletedFiles.size());
                }
            }

            if (!exactGenerationMode) {
                job = analysisJobService.createRagIndexJob(
                        project, false, JobTriggerSource.WEBHOOK, branchName, commitHash);
                analysisJobService.startJob(job);
                analysisJobService.info(job, "rag_init",
                        String.format(
                                "Starting incremental RAG update for branch '%s' (commit: %s) - %d files to update, %d to delete",
                                branchName, commitHash, addedOrModifiedSize, deletedFiles.size()));
                if (unrecognizedLegacyDiff) {
                    throw new IOException(
                            "RAG checkpoint diff was non-empty but contained no recognizable file changes");
                }
            }

            Optional<String> ragLockKey = analysisLockService.acquireLock(
                    project,
                    branchName,
                    AnalysisLockType.RAG_INDEXING,
                    commitHash,
                    null);

            if (ragLockKey.isEmpty()) {
                log.info("RAG update already in progress for project={}, branch={}; "
                                + "deferring this revision to a later trigger",
                        project.getId(), branchName);
                String reason = "RAG update already in progress; this revision was skipped and "
                        + "the previous checkpoint is retained for the next trigger";
                if (job != null) {
                    analysisJobService.info(job, "rag_skip", reason);
                    analysisJobService.skipJob(job, reason);
                }
                emitEvent(eventConsumer, Map.of(
                        "type", "info",
                        "state", "rag_skip",
                        "message", reason));
                return false;
            }

            BranchIndexBuildAdmissionService.AdmittedBuild admittedBuild = null;
            boolean exactExecutionStarted = false;
            boolean exactPublicationCompleted = false;
            LegacyRagJobLeaseService.JobLease legacyJobLease = null;
            AnalysisLockService.LockLease legacyLockLease = null;
            try {
                if (!exactGenerationMode) {
                    if (legacyRagJobLeaseService == null
                            || job == null
                            || job.getId() == null) {
                        throw new IllegalStateException(
                                "Legacy RAG job lease service is unavailable");
                    }
                    legacyJobLease = legacyRagJobLeaseService.start(job.getId());
                    legacyLockLease = analysisLockService.maintainLockLease(
                            ragLockKey.get(), Math.max(1, legacyRagLockLeaseMinutes));
                    requireLegacyOwnership(legacyJobLease, legacyLockLease);
                }
                RagBranchIndexRepository.ActiveGenerationCoordinates sourceGeneration = null;
                if (exactGenerationMode) {
                    boolean existingExactIndex = ragBranchIndexRepository
                            .existsByProjectIdAndBranchName(project.getId(), branchName);
                    if (existingExactIndex) {
                        int accessed = ragBranchIndexRepository.markAccessedIfUnclaimed(
                                project.getId(), branchName, OffsetDateTime.now());
                        if (accessed == 0) {
                            log.info("Skipping exact RAG update while transient cleanup owns "
                                            + "project={}, branch={}",
                                    project.getId(), branchName);
                            String reason = "Temporary branch index cleanup is in progress; "
                                    + "the update will retry later";
                            emitEvent(eventConsumer, Map.of(
                                    "type", "info",
                                    "state", "rag_skipped",
                                    "message", reason));
                            return false;
                        }
                        sourceGeneration = ragBranchIndexRepository
                                .findActiveGenerationCoordinates(project.getId(), branchName)
                                .orElse(null);
                    }

                    if (sourceGeneration != null
                            && sourceGeneration.getRevision().equals(commitHash)) {
                        String message = String.format(
                                "RAG generation for branch '%s' already represents commit %s",
                                branchName, commitHash);
                        if (tracksProjectStatus) {
                            // Publication is authoritative. Repair a project
                            // checkpoint left stale by a crash in one atomic
                            // terminal transition, without creating an
                            // ownerless UPDATING window.
                            ragIndexTrackingService.preparePublishedGenerationForUpdate(
                                    project, branchName, commitHash,
                                    sourceGeneration.getFileCount(),
                                    sourceGeneration.getChunkCount());
                        }
                        emitEvent(eventConsumer, Map.of(
                                "type", "info",
                                "state", "rag_complete",
                                "message", message));
                        return true;
                    }

                    if (branchIndexBuildAdmissionService == null) {
                        throw new IllegalStateException(
                                "Exact RAG build admission service is unavailable");
                    }
                    VcsRepoBinding exactBinding = project.getVcsRepoBinding();
                    if (exactBinding == null || exactBinding.getVcsConnection() == null
                            || exactBinding.getExternalNamespace() == null
                            || exactBinding.getExternalNamespace().isBlank()
                            || exactBinding.getExternalRepoSlug() == null
                            || exactBinding.getExternalRepoSlug().isBlank()) {
                        throw new IllegalStateException(
                                "Project has no complete VcsRepoBinding configured");
                    }
                    if (project.getConfiguration() == null
                            || project.getConfiguration().ragConfig() == null) {
                        throw new IllegalStateException(
                                "Project has no RAG configuration");
                    }
                    admittedBuild = branchIndexBuildAdmissionService.admit(
                            project,
                            branchName,
                            commitHash,
                            exactGenerationKind,
                            JobTriggerSource.WEBHOOK,
                            ragLockKey.get(),
                            BranchIndexBuildAdmissionService.BuildOrigin.AUTOMATIC);
                    job = admittedBuild.job();
                    analysisJobService.info(job, "rag_init",
                            String.format(
                                    "Starting exact RAG generation rebuild for branch '%s' (commit: %s)",
                                    branchName, commitHash));
                }

                emitEvent(eventConsumer, exactGenerationMode
                        ? Map.of(
                                "type", "status",
                                "state", "rag_update",
                                "message", String.format(
                                        "Building exact RAG snapshot for branch '%s' at commit %s",
                                        branchName, commitHash))
                        : Map.of(
                                "type", "status",
                                "state", "rag_update",
                                "message", "Updating RAG index with "
                                        + (addedOrModifiedSize + deletedFiles.size())
                                        + " changed files"));

                if (tracksProjectStatus && !exactGenerationMode) {
                    ragIndexTrackingService.markUpdatingStarted(
                            project, branchName, commitHash, job != null ? job.getId() : null);
                }

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

                Map<String, Object> result;
                if (exactGenerationMode) {
                    if (branchGenerationBuildService == null) {
                        throw new IllegalStateException(
                                "Exact RAG generation build service is unavailable");
                    }
                    var ragConfig = project.getConfiguration().ragConfig();
                    // A same-revision active generation returned above. Any
                    // remaining mismatch must create and activate a fresh
                    // snapshot, including A -> B -> A where an older A
                    // generation already succeeded but is no longer active.
                    exactExecutionStarted = true;
                    result = branchGenerationBuildService.execute(
                            project,
                            vcsConnection,
                            workspaceSlug,
                            repoSlug,
                            branchName,
                            commitHash,
                            exactGenerationKind,
                            ragConfig.includePatterns(),
                            ragConfig.excludePatterns(),
                            admittedBuild.preparedBuild(),
                            null);
                    exactPublicationCompleted = true;
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

                if (!exactGenerationMode) {
                    confirmLegacyOwnership(legacyJobLease, legacyLockLease);
                }

                int documentCount = result.get("document_count") instanceof Number number
                        ? number.intValue() : 0;
                int filesUpdated = exactGenerationMode
                        ? documentCount
                        : ((Number) result.getOrDefault("updatedFiles", 0)).intValue();
                int filesDeleted = ((Number) result.getOrDefault("deletedFiles", 0)).intValue();
                int filesSkipped = ((Number) result.getOrDefault("skippedFiles", 0)).intValue();
                Integer newlyAddedFilesCount = (Integer) result.get("addedFilesCount");

                Integer chunkCount = null;
                if (result.get("chunk_count") != null) {
                    chunkCount = ((Number) result.get("chunk_count")).intValue();
                }

                if (tracksProjectStatus && exactGenerationMode) {
                    ragIndexTrackingService.reconcilePublishedGeneration(
                            project,
                            branchName,
                            commitHash,
                            documentCount,
                            chunkCount,
                            job != null ? job.getId() : null);
                }

                if (!exactGenerationMode) {
                    if (legacyRagUpdateCompletionService == null
                            || job == null
                            || job.getId() == null
                            || !legacyRagUpdateCompletionService.complete(
                                    project,
                                    branchName,
                                    commitHash,
                                    job.getId(),
                                    legacyJobLease.validAfter(),
                                    tracksProjectStatus,
                                    newlyAddedFilesCount != null
                                            ? newlyAddedFilesCount : 0,
                                    filesDeleted,
                                    chunkCount,
                                    deletedFiles)) {
                        throw new LegacyRagOwnershipLostException(
                                "Legacy RAG update lost durable ownership before publication");
                    }
                }

                String completionMessage;
                if (exactGenerationMode) {
                    completionMessage = String.format(
                            "Exact RAG snapshot activated: %d documents, %d chunks",
                            documentCount, chunkCount != null ? chunkCount : 0);
                } else if (checkpointOnlyAdvance) {
                    completionMessage = String.format(
                            "RAG checkpoint advanced for branch '%s' to commit %s; no files changed",
                            branchName, commitHash);
                } else {
                    completionMessage = String.format(
                            "RAG index updated: %d files updated, %d deleted, %d non-text files skipped",
                            filesUpdated, filesDeleted, filesSkipped);
                }
                emitEvent(eventConsumer, Map.of(
                        "type", "status",
                        "state", "rag_complete",
                        "message", completionMessage));

                log.info("RAG incremental update completed for project={}: {} files updated, {} deleted, "
                                + "{} non-text files skipped",
                        project.getId(), filesUpdated, filesDeleted, filesSkipped);
                if (exactGenerationMode) {
                    analysisJobService.info(job, "rag_complete", completionMessage);
                    analysisJobService.completeJob(job, null);
                } else {
                    try {
                        analysisJobService.recordExternallyCompletedJob(
                                job, "rag_complete", completionMessage);
                    } catch (Exception notificationFailure) {
                        // The job and checkpoints already committed atomically.
                        // A local observer failure cannot reverse that outcome.
                        log.warn(
                                "Could not announce completed legacy RAG job {}: {}",
                                job != null ? job.getId() : null,
                                notificationFailure.getMessage());
                    }
                }
                return true;

            } catch (Exception e) {
                // execute owns operation failure after it starts. Fail the
                // admitted operation here only when a local hand-off step
                // between admission and execute raised first.
                if (admittedBuild != null && !exactExecutionStarted) {
                    try {
                        branchIndexBuildAdmissionService.abortOperation(
                                admittedBuild,
                                e.getMessage() != null
                                        ? e.getMessage()
                                        : e.getClass().getSimpleName());
                    } catch (Exception abortFailure) {
                        log.error("Could not terminalize admitted exact RAG build {}",
                                admittedBuild.preparedBuild().operationId(), abortFailure);
                    }
                }
                boolean legacyOwnershipLost =
                        e instanceof LegacyRagOwnershipLostException
                                || e instanceof LegacyRagUpdateCompletionService
                                    .LegacyRagCompletionConflictException;
                if (exactPublicationCompleted) {
                    // execute returns only after the registry operation is
                    // SUCCEEDED and the generation is active. A later status,
                    // job-log, or job-completion failure is projection drift,
                    // not a failed build. Preserve its RUNNING ownership for
                    // RagIndexOperationRecoveryService to finish idempotently.
                    log.error(
                            "Exact RAG generation was published but its projections could not be finalized: "
                                    + "project={}, branch={}, job={}",
                            project.getId(), branchName,
                            job != null ? job.getId() : null, e);
                } else if (legacyOwnershipLost) {
                    // Recovery may already own the job/status terminal
                    // transition. Never overwrite that durable outcome from a
                    // producer that can no longer prove ownership.
                    log.info("Legacy RAG update stopped after ownership loss: {}",
                            e.getMessage());
                } else {
                    if (tracksProjectStatus) {
                        if (exactGenerationMode && admittedBuild != null
                                && admittedBuild.statusAdmission()
                                    == BranchIndexBuildAdmissionService.ProjectStatusAdmission.INDEXING) {
                            ragIndexTrackingService.markIndexingFailed(
                                    project, e.getMessage(),
                                    job != null ? job.getId() : null);
                        } else {
                            // Incremental failure preserves the preceding
                            // completed checkpoint and usable index.
                            ragIndexTrackingService.markIncrementalUpdateFailed(
                                    project, e.getMessage(),
                                    job != null ? job.getId() : null);
                        }
                    }
                    log.error("RAG incremental update failed", e);
                    if (job != null) {
                        analysisJobService.failJob(
                                job, "RAG incremental update failed: " + e.getMessage());
                    }
                }
                emitEvent(eventConsumer, Map.of(
                        "type", "warning",
                        "state", "rag_error",
                        "message", "RAG incremental update failed: " + e.getMessage()));
                return false;
            } finally {
                if (legacyJobLease != null) {
                    legacyJobLease.close();
                }
                if (legacyLockLease != null) {
                    legacyLockLease.close();
                }
                try {
                    analysisLockService.releaseLock(ragLockKey.get());
                } catch (RuntimeException releaseFailure) {
                    // Publication/job terminalization is authoritative. A lock
                    // cleanup outage must not reverse a completed operation;
                    // exact recovery or the lock TTL will remove the row.
                    log.info(
                            "RAG indexing lock could not be released after processing; "
                                    + "leaving it to recovery/expiry: project={}, branch={}, detail={}",
                            project.getId(), branchName, releaseFailure.getMessage());
                }
            }
        } catch (Exception e) {
            log.warn("RAG incremental update failed (non-critical): {}", e.getMessage());
            if (job != null) {
                analysisJobService.failJob(
                        job, "RAG incremental update failed: " + e.getMessage());
            }
            emitEvent(eventConsumer, Map.of(
                    "type", "warning",
                    "state", "rag_error",
                    "message", "RAG incremental update failed: " + e.getMessage()));
            return false;
        }
    }

    private static void requireLegacyOwnership(
            LegacyRagJobLeaseService.JobLease jobLease,
            AnalysisLockService.LockLease lockLease) {
        if (jobLease.isOwnershipLost() || lockLease.isOwnershipLost()) {
            throw new LegacyRagOwnershipLostException(
                    "Legacy RAG update lost durable ownership before remote mutation");
        }
    }

    private static void confirmLegacyOwnership(
            LegacyRagJobLeaseService.JobLease jobLease,
            AnalysisLockService.LockLease lockLease) {
        if (!jobLease.confirmOwnership() || !lockLease.confirmOwnership()) {
            throw new LegacyRagOwnershipLostException(
                    "Legacy RAG update lost durable ownership before publication");
        }
    }

    private static final class LegacyRagOwnershipLostException
            extends IllegalStateException {
        private LegacyRagOwnershipLostException(String message) {
            super(message);
        }
    }

    /**
     * Rebuilds the effective range from the last completed RAG checkpoint.
     * The caller's branch-analysis diff is used only when no completed
     * checkpoint exists yet for this branch.
     */
    private LegacyDiffResolution resolveDiffFromCompletedCheckpoint(
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
            return new LegacyDiffResolution(suppliedDiff, false);
        }
        if (checkpoint.equals(commitHash)) {
            log.info("RAG checkpoint already represents branch {} commit {}", branchName, commitHash);
            return new LegacyDiffResolution("", true);
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
        return new LegacyDiffResolution(catchUpDiff != null ? catchUpDiff : "", false);
    }

    private record LegacyDiffResolution(String rawDiff, boolean alreadyCurrent) {
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
    public void createOrUpdateBranchIndex(
            Project project,
            String branchName,
            String baseBranch,
            String branchCommit,
            String rawDiff,
            Consumer<Map<String, Object>> eventConsumer) {
        if (!isRagEnabled(project)) {
            return;
        }
        if (!branchName.equals(getBaseBranch(project))
                && !shouldHaveBranchIndex(project, branchName)) {
            log.info("Skipping branch index mutation for non-retained branch: project={}, branch={}",
                    project.getId(), branchName);
            emitEvent(eventConsumer, Map.of(
                    "type", "info",
                    "state", "rag_skipped",
                    "message", "Branch is not configured as a retained RAG branch"));
            return;
        }
        // Dispatch only after this branch-push compatibility entry point has
        // enforced durable ownership. The trigger selects legacy or exact mode.
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

        if (!isRagIndexReady(project) && !usesExactGenerations(project)) {
            log.warn("Cannot update branch index - base RAG index not ready for project={}", project.getId());
            return false;
        }

        if (!targetBranch.equals(getBaseBranch(project))
                && !shouldHaveBranchIndex(project, targetBranch)) {
            log.info("Skipping branch index update for non-retained branch: project={}, branch={}",
                    project.getId(), targetBranch);
            emitEvent(eventConsumer, Map.of(
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
            if (usesExactGenerations(project)) {
                if (isExactGenerationCurrent(
                        project, targetBranch, targetCommit, false)) {
                    log.info("Exact branch generation already represents project={}, branch={}, commit={}",
                            project.getId(), targetBranch, targetCommit);
                    return true;
                }
                log.info("Binding exact branch update after acquiring its RAG lock: "
                                + "project={}, branch={}, target={}",
                        project.getId(), targetBranch, targetCommit);
                return triggerIncrementalUpdate(
                        project, targetBranch, targetCommit, "", eventConsumer);
            }

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

            emitEvent(eventConsumer, Map.of(
                    "type", "status",
                    "state", "branch_index",
                    "message", String.format("Calculating diff between '%s' and '%s'", baseBranch, targetBranch)));

            // Compatibility path for a branch that has no checkpoint yet. The exact
            // generation builder replaces this with a complete verified snapshot.
            String rawDiff = vcsClient.getBranchDiff(workspaceSlug, repoSlug, baseBranch, targetBranch);

            if (rawDiff == null || rawDiff.isEmpty()) {
                log.info("No diff between {} and {} - branch has same content as base", baseBranch, targetBranch);
                emitEvent(eventConsumer, Map.of(
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
            emitEvent(eventConsumer, Map.of(
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
            emitEvent(eventConsumer, Map.of(
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

            emitEvent(eventConsumer, Map.of(
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
                emitEvent(eventConsumer, Map.of(
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
            emitEvent(eventConsumer, Map.of(
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
        return deleteBranchIndexWithOutcome(project, branchName, eventConsumer).successful();
    }

    private BranchIndexDeletionResult deleteBranchIndexWithOutcome(
            Project project,
            String branchName,
            Consumer<Map<String, Object>> eventConsumer) {
        if (!isRagEnabled(project)) {
            log.debug("RAG not enabled for project={}", project.getId());
            return new BranchIndexDeletionResult(false, false);
        }

        String baseBranch = getBaseBranch(project);
        if (branchName.equals(baseBranch)) {
            log.warn("Cannot delete main branch index for project={}", project.getId());
            emitEvent(eventConsumer, Map.of(
                    "type", "warning",
                    "message", "Cannot delete main branch index"));
            return new BranchIndexDeletionResult(false, false);
        }

        VcsRepoBinding vcsRepoBinding = project.getVcsRepoBinding();
        if (vcsRepoBinding == null) {
            log.error("Project has no VcsRepoBinding configured");
            return new BranchIndexDeletionResult(false, false);
        }

        String workspaceSlug = vcsRepoBinding.getExternalNamespace();
        String projectSlug = vcsRepoBinding.getExternalRepoSlug();

        try {
            log.info("Deleting branch index for project={}, branch={}", project.getId(), branchName);

            emitEvent(eventConsumer, Map.of(
                    "type", "status",
                    "state", "branch_delete",
                    "message", String.format("Deleting RAG index for branch '%s'", branchName)));

            boolean success;
            boolean stopRemainingBranches = false;
            Optional<RagBranchIndex> trackedIndex = ragBranchIndexRepository
                    .findByProjectIdAndBranchName(project.getId(), branchName);
            List<RagBranchIndexGeneration> generations = trackedIndex.isPresent()
                    && branchGenerationRepository != null
                    ? branchGenerationRepository.findByBranchIndexIdOrderByCreatedAtDesc(
                            trackedIndex.get().getId())
                    : List.of();
            if (!generations.isEmpty()) {
                generations = generations.stream()
                        .sorted(Comparator.comparing(generation ->
                                generation.getStatus()
                                        == RagBranchIndexGenerationStatus.ACTIVE))
                        .toList();
                success = true;
                for (RagBranchIndexGeneration generation : generations) {
                    if (generation.getStatus() == RagBranchIndexGenerationStatus.ACTIVE
                            && !success) {
                        // A failed older-target deletion must not leave the
                        // registry pointing at a destroyed active target.
                        break;
                    }
                    RagPipelineClient.BranchDeletionOutcome outcome =
                            ragPipelineClient.deleteBranchWithOutcome(
                                    project.getWorkspace().getName(), project.getNamespace(),
                                    branchName, generation.getCollectionName(),
                                    generation.getRevision(), generation.getManifestDigest());
                    if (!outcome.successful()) {
                        success = false;
                        if (isRagDisabled(outcome)) {
                            return new BranchIndexDeletionResult(false, true);
                        }
                        logBranchCleanupFailure(project, branchName, outcome);
                        if (outcome.shouldStopRemainingTargets()) {
                            stopRemainingBranches = true;
                            break;
                        }
                    }
                }
            } else {
                // Backward-compatible cleanup for the legacy shared collection.
                RagPipelineClient.BranchDeletionOutcome outcome =
                        ragPipelineClient.deleteBranchWithOutcome(
                                workspaceSlug, projectSlug, branchName, null);
                success = outcome.successful();
                if (!success) {
                    if (isRagDisabled(outcome)) {
                        return new BranchIndexDeletionResult(false, true);
                    }
                    logBranchCleanupFailure(project, branchName, outcome);
                    stopRemainingBranches = outcome.shouldStopRemainingTargets();
                }
            }

            if (success) {
                // Clean up database tracking
                ragBranchIndexRepository.deleteByProjectIdAndBranchName(project.getId(), branchName);

                log.info("Successfully deleted branch index for project={}, branch={}", project.getId(), branchName);
                emitEvent(eventConsumer, Map.of(
                        "type", "success",
                        "message", String.format("Deleted RAG index for branch '%s'", branchName)));
                return new BranchIndexDeletionResult(true, false);
            } else {
                return new BranchIndexDeletionResult(false, stopRemainingBranches);
            }

        } catch (Exception e) {
            log.error("Failed to delete branch index for project={}, branch={}",
                    project.getId(), branchName, e);
            emitEvent(eventConsumer, Map.of(
                    "type", "error",
                    "message", "Failed to delete branch index: " + e.getMessage()));
            return new BranchIndexDeletionResult(false, true);
        }
    }

    private record BranchIndexDeletionResult(
            boolean successful,
            boolean shouldStopRemainingBranches) {
    }

    private static void logBranchCleanupFailure(
            Project project,
            String branchName,
            RagPipelineClient.BranchDeletionOutcome outcome) {
        log.warn("Failed to delete branch RAG generation for project={}, branch={}, target={}: "
                        + "status={} detail={}",
                project.getId(), branchName, outcome.targetLabel(),
                outcome.statusCode() != null ? outcome.statusCode() : outcome.failure(),
                outcome.detail());
    }

    private static boolean isRagDisabled(
            RagPipelineClient.BranchDeletionOutcome outcome) {
        return outcome.statusCode() == null
                && outcome.failure() == RagPipelineClient.BranchDeletionFailure.TARGET
                && "RAG disabled".equals(outcome.detail());
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

            emitEvent(eventConsumer, Map.of(
                    "type", "status",
                    "state", "cleanup",
                    "message", String.format("Cleaning up %d stale branches", staleBranches.size())));

            List<String> deletedBranches = new ArrayList<>();
            List<String> failedBranches = new ArrayList<>();

            for (String branch : staleBranches) {
                try {
                    BranchIndexDeletionResult deletion =
                            deleteBranchIndexWithOutcome(project, branch, eventConsumer);
                    if (deletion.successful()) {
                        deletedBranches.add(branch);
                    } else {
                        failedBranches.add(branch);
                        if (deletion.shouldStopRemainingBranches()) {
                            log.info("Stopping stale branch cleanup after a service-wide deletion "
                                            + "failure; {} remaining branch(es) will retry next run",
                                    staleBranches.size()
                                            - deletedBranches.size()
                                            - failedBranches.size());
                            break;
                        }
                    }
                } catch (Exception e) {
                    log.warn("Failed to delete stale branch {} for project={}: {}",
                            branch, project.getId(), e.getMessage());
                    failedBranches.add(branch);
                }
            }

            log.info("Cleanup complete for project={}: deleted={}, failed={}",
                    project.getId(), deletedBranches.size(), failedBranches.size());

            emitEvent(eventConsumer, Map.of(
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
                emitEvent(eventConsumer, Map.of(
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
            emitEvent(eventConsumer, Map.of(
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
        if (!isRagIndexReady(project) && !usesExactGenerations(project)) {
            log.debug("Main RAG index not ready for project={}", project.getId());
            return false;
        }

        // Get current commit on branch
        String currentCommit = vcsClient.getLatestCommitHash(workspaceSlug, repoSlug, branchName);

        if (usesExactGenerations(project)) {
            if (isExactGenerationCurrent(
                    project, branchName, currentCommit, true)) {
                log.debug("Exact main RAG generation and project checkpoint are up-to-date "
                                + "for project={}, commit={}",
                        project.getId(), currentCommit);
                return true;
            }
            log.info("Exact main RAG generation requires reconciliation for project={}, "
                            + "branch={}, target={}",
                    project.getId(), branchName, currentCommit);
            return triggerIncrementalUpdate(
                    project, branchName, currentCommit, "", eventConsumer);
        }

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

        emitEvent(eventConsumer, Map.of(
                "type", "status",
                "state", "rag_update",
                "message", String.format("Updating RAG index from %s to %s",
                        indexedCommit.substring(0, 7), currentCommit.substring(0, 7))));

        // The locked trigger owns the checkpoint-to-target compare, durable
        // child job, and terminal checkpoint transition. In particular, an
        // empty compare must not update the checkpoint directly here.
        return triggerIncrementalUpdate(
                project, branchName, currentCommit, "", eventConsumer);
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

        if (usesExactGenerations(project)) {
            if (isExactGenerationCurrent(
                    project, targetBranch, currentCommit, false)) {
                log.info("Exact branch generation is up-to-date for project={}, branch={}, commit={}",
                        project.getId(), targetBranch, currentCommit);
                return true;
            }
            log.info("Exact branch generation requires reconciliation for project={}, "
                            + "branch={}, target={}",
                    project.getId(), targetBranch, currentCommit);
            return triggerIncrementalUpdate(
                    project, targetBranch, currentCommit, "", eventConsumer);
        }

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

        emitEvent(eventConsumer, Map.of(
                "type", "status",
                "state", "branch_update",
                "message",
                String.format("Reconciling branch %s index from %s to %s",
                        targetBranch, indexedCommit, currentCommit)));

        // The trigger reacquires the complete range from this branch's durable
        // checkpoint and owns both the job and checkpoint transition.
        log.info("Triggering incremental branch reconciliation for '{}'", targetBranch);
        return triggerIncrementalUpdate(
                project, targetBranch, currentCommit, "", eventConsumer);
    }

    /**
     * A cheap exact-generation readiness hint that is safe against transient
     * cleanup. The atomic touch must win before the scalar projection is read;
     * otherwise the locked trigger owns reconciliation. Primary callers also
     * require the independently persisted project checkpoint to be current so
     * a publication/status crash is repaired by the trigger's locked no-op.
     */
    private boolean isExactGenerationCurrent(
            Project project,
            String branchName,
            String targetRevision,
            boolean requireProjectCheckpoint) {
        if (ragBranchIndexRepository.markAccessedIfUnclaimed(
                project.getId(), branchName, OffsetDateTime.now()) == 0) {
            return false;
        }
        boolean generationCurrent = ragBranchIndexRepository
                .findActiveGenerationCoordinates(project.getId(), branchName)
                .map(RagBranchIndexRepository.ActiveGenerationCoordinates::getRevision)
                .filter(targetRevision::equals)
                .isPresent();
        if (!generationCurrent || !requireProjectCheckpoint) {
            return generationCurrent;
        }
        return ragIndexTrackingService.getIndexStatus(project)
                .map(RagIndexStatus::getIndexedCommitHash)
                .filter(targetRevision::equals)
                .isPresent();
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

    /** Observer delivery is never part of the durable RAG operation outcome. */
    private static void emitEvent(
            Consumer<Map<String, Object>> eventConsumer,
            Map<String, Object> event) {
        if (eventConsumer == null) {
            return;
        }
        try {
            eventConsumer.accept(event);
        } catch (RuntimeException observerFailure) {
            log.debug("RAG progress observer rejected event state={}: {}",
                    event.get("state"), observerFailure.getMessage());
        }
    }

}
