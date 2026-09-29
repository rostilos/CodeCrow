package org.rostilos.codecrow.ragengine.service;

import org.rostilos.codecrow.analysisapi.rag.RagOperationsService;
import org.rostilos.codecrow.analysisengine.service.AnalysisLockService;
import org.rostilos.codecrow.core.model.analysis.AnalysisLockType;
import org.rostilos.codecrow.core.model.analysis.RagIndexStatus;
import org.rostilos.codecrow.core.model.job.Job;
import org.rostilos.codecrow.core.model.job.JobTriggerSource;
import org.rostilos.codecrow.core.model.project.Project;
import org.rostilos.codecrow.core.model.rag.RagBranchIndex;
import org.rostilos.codecrow.core.model.rag.RagBranchIndexGeneration;
import org.rostilos.codecrow.core.model.rag.RagBranchIndexGenerationStatus;
import org.rostilos.codecrow.core.model.rag.RagBranchIndexKind;
import org.rostilos.codecrow.core.model.vcs.VcsRepoBinding;
import org.rostilos.codecrow.core.persistence.repository.rag.RagBranchIndexGenerationRepository;
import org.rostilos.codecrow.core.persistence.repository.rag.RagBranchIndexRepository;
import org.rostilos.codecrow.core.service.AnalysisJobService;
import org.rostilos.codecrow.core.service.RepositoryIndexJobQueueService;
import org.rostilos.codecrow.ragengine.branch.BranchIndexBuildAdmissionService;
import org.rostilos.codecrow.ragengine.branch.BranchIndexGenerationBuildService;
import org.rostilos.codecrow.ragengine.client.RagPipelineClient;
import org.slf4j.Logger;
import org.slf4j.LoggerFactory;
import org.springframework.beans.factory.annotation.Value;
import org.springframework.stereotype.Service;

import java.time.OffsetDateTime;
import java.util.ArrayList;
import java.util.Comparator;
import java.util.HashSet;
import java.util.List;
import java.util.Map;
import java.util.Optional;
import java.util.Set;
import java.util.function.Consumer;

/**
 * Owns branch-bound immutable repository-index generations.
 *
 * <p>Every RAG-enabled project uses the same path: acquire the branch lock,
 * admit a durable operation and job, build a complete physical generation,
 * then publish it atomically through the branch registry. {@link RagIndexStatus}
 * is only the UI/job projection of the primary branch's active generation.</p>
 */
@Service
public class RagOperationsServiceImpl implements RagOperationsService {
    private static final Logger log = LoggerFactory.getLogger(
            RagOperationsServiceImpl.class);

    private final RagIndexTrackingService trackingService;
    private final AnalysisLockService lockService;
    private final AnalysisJobService jobService;
    private final RagBranchIndexRepository branchIndexRepository;
    private final RagBranchIndexGenerationRepository generationRepository;
    private final RagPipelineClient pipelineClient;
    private final BranchIndexGenerationBuildService generationBuildService;
    private final BranchIndexBuildAdmissionService buildAdmissionService;
    private final RepositoryIndexJobQueueService queueService;
    private final RagRepresentationIdentityService representationIdentityService;

    @Value("${codecrow.rag.api.enabled:true}")
    private boolean ragApiEnabled;

    public RagOperationsServiceImpl(
            RagIndexTrackingService trackingService,
            AnalysisLockService lockService,
            AnalysisJobService jobService,
            RagBranchIndexRepository branchIndexRepository,
            RagBranchIndexGenerationRepository generationRepository,
            RagPipelineClient pipelineClient,
            BranchIndexGenerationBuildService generationBuildService,
            BranchIndexBuildAdmissionService buildAdmissionService,
            RepositoryIndexJobQueueService queueService,
            RagRepresentationIdentityService representationIdentityService) {
        this.trackingService = trackingService;
        this.lockService = lockService;
        this.jobService = jobService;
        this.branchIndexRepository = branchIndexRepository;
        this.generationRepository = generationRepository;
        this.pipelineClient = pipelineClient;
        this.generationBuildService = generationBuildService;
        this.buildAdmissionService = buildAdmissionService;
        this.queueService = queueService;
        this.representationIdentityService = representationIdentityService;
    }

    @Override
    public boolean isRagPipelineHealthy() {
        return pipelineClient.isHealthy();
    }

    @Override
    public boolean isRagEnabled(Project project) {
        if (!ragApiEnabled || project == null) {
            return false;
        }
        var config = project.getConfiguration();
        return config != null
                && config.ragConfig() != null
                && config.ragConfig().enabled();
    }

    @Override
    public boolean isRagIndexReady(Project project) {
        return isRagEnabled(project)
                && activeGeneration(project, getBaseBranch(project)).isPresent();
    }

    @Override
    public boolean isBranchIndexReady(Project project, String branchName) {
        return isRagEnabled(project)
                && branchName != null
                && activeGeneration(project, branchName).isPresent();
    }

    private Optional<RagBranchIndexRepository.ActiveGenerationCoordinates>
            activeGeneration(Project project, String branchName) {
        if (!branchIndexRepository.existsByProjectIdAndBranchName(
                project.getId(), branchName)) {
            return Optional.empty();
        }
        if (branchIndexRepository.markAccessedIfUnclaimed(
                project.getId(), branchName, OffsetDateTime.now()) == 0) {
            return Optional.empty();
        }
        return branchIndexRepository.findActiveGenerationCoordinates(
                project.getId(), branchName);
    }

    /**
     * Compatibility entry point used by branch analysis. The diff is not an
     * indexing input: every accepted revision creates a complete immutable
     * generation from the provider snapshot.
     */
    @Override
    public boolean refreshBranchGeneration(
            Project project,
            String branchName,
            String revisionValue,
            Consumer<Map<String, Object>> events) {
        if (!isRagEnabled(project)) {
            return false;
        }
        String branch = normalizeRequired(branchName, "branchName");
        String revision = normalizeRequired(revisionValue, "revision");
        String primary = getBaseBranch(project);
        if (!branch.equals(primary) && !shouldHaveBranchIndex(project, branch)) {
            emitEvent(events, Map.of(
                    "type", "info",
                    "state", "rag_skipped",
                    "message", "Branch is outside the configured analysis target/push patterns"));
            return false;
        }
        Job queued = queueService.enqueue(
                project, branch, revision, JobTriggerSource.WEBHOOK);
        emitEvent(events, Map.of(
                "type", "status",
                "state", "rag_queued",
                "message", "Repository generation queued for branch '" + branch + "'",
                "jobId", queued.getExternalId()));
        return true;
    }

    @Override
    public boolean executeQueuedBranchGeneration(
            Project project,
            String branchName,
            String revisionValue,
            Job queuedJob) {
        if (!isRagEnabled(project)) {
            if (queuedJob != null) {
                jobService.skipJob(queuedJob, "Repository indexing is disabled");
            }
            return false;
        }

        Consumer<Map<String, Object>> events = null;
        String branch = normalizeRequired(branchName, "branchName");
        String revision = normalizeRequired(revisionValue, "revision");
        String primary = getBaseBranch(project);
        if (!branch.equals(primary)
                && !shouldHaveBranchIndex(project, branch)) {
            if (queuedJob != null) {
                jobService.skipJob(
                        queuedJob,
                        "Branch is outside the configured analysis target/push patterns");
            }
            emitEvent(events, Map.of(
                    "type", "info",
                    "state", "rag_skipped",
                    "message", "Branch is outside the configured analysis target/push patterns"));
            return false;
        }

        Optional<String> lock = lockService.acquireLock(
                project,
                branch,
                AnalysisLockType.RAG_INDEXING,
                revision,
                null);
        if (lock.isEmpty()) {
            emitEvent(events, Map.of(
                    "type", "info",
                    "state", "rag_skip",
                    "message", "Repository indexing is already running for this branch"));
            return false;
        }

        Job job = queuedJob;
        BranchIndexBuildAdmissionService.AdmittedBuild admission = null;
        boolean executionStarted = false;
        boolean publicationCompleted = false;
        boolean primaryBranch = branch.equals(primary);
        boolean operatorRefresh = queuedJob != null
                && queuedJob.getTriggerSource() == JobTriggerSource.UI;
        try {
            Optional<RagBranchIndexRepository.ActiveGenerationCoordinates> active =
                    activeGeneration(project, branch);
            Optional<String> representationFingerprint =
                    representationIdentityService.currentProjectFingerprint(project);
            if (active.isPresent()
                    && revision.equals(active.get().getRevision())
                    && !operatorRefresh
                    && (representationFingerprint.isEmpty()
                        || representationFingerprint.get().equals(
                                active.get().getRepresentationFingerprint()))) {
                if (primaryBranch) {
                    trackingService.preparePublishedGenerationForUpdate(
                            project,
                            branch,
                            revision,
                            active.get().getFileCount(),
                            active.get().getChunkCount(),
                            active.get().getActivatedAt());
                }
                if (job != null) {
                    jobService.completeJob(job, Map.of(
                            "status", "reused",
                            "branch", branch,
                            "revision", revision));
                }
                return true;
            }

            VcsRepoBinding binding = requireBinding(project);
            var ragConfig = project.getConfiguration().ragConfig();
            RagBranchIndexKind kind = indexKind(project, branch);
            admission = buildAdmissionService.admit(
                    project,
                    branch,
                    revision,
                    kind,
                    queuedJob != null && queuedJob.getTriggerSource() != null
                            ? queuedJob.getTriggerSource()
                            : JobTriggerSource.SCHEDULED,
                    lock.get(),
                    operatorRefresh
                            ? BranchIndexBuildAdmissionService.BuildOrigin.OPERATOR
                            : BranchIndexBuildAdmissionService.BuildOrigin.AUTOMATIC,
                    representationFingerprint.orElse(null),
                    queuedJob);
            job = admission.job();
            jobService.info(
                    job,
                    "rag_init",
                    "Building exact repository-index generation for branch '"
                            + branch + "' at commit " + revision);
            emitEvent(events, Map.of(
                    "type", "status",
                    "state", "rag_update",
                    "message", "Building exact repository snapshot for branch '"
                            + branch + "' at commit " + revision));

            executionStarted = true;
            Map<String, Object> result = generationBuildService.execute(
                    project,
                    binding.getVcsConnection(),
                    binding.getExternalNamespace(),
                    binding.getExternalRepoSlug(),
                    branch,
                    revision,
                    kind,
                    listOrEmpty(ragConfig.includePatterns()),
                    listOrEmpty(ragConfig.excludePatterns()),
                    admission.preparedBuild(),
                    null);
            publicationCompleted = true;

            int documents = number(result.get("document_count"));
            int chunks = number(result.get("chunk_count"));
            if (primaryBranch) {
                trackingService.reconcilePublishedGeneration(
                        project,
                        branch,
                        revision,
                        documents,
                        chunks,
                        job.getId());
            }

            String completion = "Exact repository-index generation activated: "
                    + documents + " documents, " + chunks + " chunks";
            jobService.info(job, "rag_complete", completion);
            jobService.completeJob(job, null);
            emitEvent(events, Map.of(
                    "type", "status",
                    "state", "rag_complete",
                    "message", completion));
            return true;
        } catch (Exception failure) {
            String diagnostic = failure.getMessage() != null
                    ? failure.getMessage() : failure.getClass().getSimpleName();
            if (admission != null && !executionStarted) {
                try {
                    buildAdmissionService.abortOperation(admission, diagnostic);
                } catch (Exception abortFailure) {
                    log.error("Could not terminalize admitted repository-index build {}",
                            admission.preparedBuild().operationId(), abortFailure);
                }
            }

            if (publicationCompleted) {
                log.error(
                        "Repository-index generation was published but its projections "
                                + "could not be finalized: project={}, branch={}, job={}",
                        project.getId(), branch, job != null ? job.getId() : null,
                        failure);
            } else {
                if (primaryBranch && admission != null && job != null) {
                    if (admission.statusAdmission()
                            == BranchIndexBuildAdmissionService.ProjectStatusAdmission.INDEXING) {
                        trackingService.markIndexingFailed(
                                project, diagnostic, job.getId());
                    } else if (admission.statusAdmission()
                            == BranchIndexBuildAdmissionService.ProjectStatusAdmission.UPDATING) {
                        trackingService.markGenerationRefreshFailed(
                                project, diagnostic, job.getId());
                    }
                }
                if (job != null) {
                    jobService.failJob(job, diagnostic);
                }
                log.error("Exact repository-index generation build failed", failure);
            }
            emitEvent(events, Map.of(
                    "type", "warning",
                    "state", "rag_error",
                    "message", "Repository indexing failed: " + diagnostic));
            return false;
        } finally {
            try {
                lockService.releaseLock(lock.get());
            } catch (RuntimeException releaseFailure) {
                log.info(
                        "Repository-index lock cleanup deferred to recovery/expiry: "
                                + "project={}, branch={}, detail={}",
                        project.getId(), branch, releaseFailure.getMessage());
            }
        }
    }

    @Override
    public boolean deleteBranchIndex(
            Project project,
            String branchName,
            Consumer<Map<String, Object>> events) {
        return deleteBranchIndexWithOutcome(
                project, branchName, events).successful();
    }

    private BranchDeletionResult deleteBranchIndexWithOutcome(
            Project project,
            String branchName,
            Consumer<Map<String, Object>> events) {
        if (!isRagEnabled(project)) {
            return new BranchDeletionResult(false, false);
        }
        String branch = normalizeRequired(branchName, "branchName");
        if (branch.equals(getBaseBranch(project))) {
            emitEvent(events, Map.of(
                    "type", "warning",
                    "message", "Cannot delete the primary repository index"));
            return new BranchDeletionResult(false, false);
        }

        try {
            Optional<RagBranchIndex> tracked = branchIndexRepository
                    .findByProjectIdAndBranchName(project.getId(), branch);
            List<RagBranchIndexGeneration> generations = tracked.isPresent()
                    ? generationRepository
                        .findByBranchIndexIdOrderByCreatedAtDesc(tracked.get().getId())
                    : List.of();
            generations = generations.stream()
                    .sorted(Comparator.comparing(generation ->
                            generation.getStatus()
                                    == RagBranchIndexGenerationStatus.ACTIVE))
                    .toList();

            boolean successful = true;
            boolean stopRemaining = false;
            for (RagBranchIndexGeneration generation : generations) {
                if (generation.getStatus()
                        == RagBranchIndexGenerationStatus.ACTIVE
                        && !successful) {
                    break;
                }
                RagPipelineClient.BranchDeletionOutcome outcome =
                        pipelineClient.deleteBranchWithOutcome(
                                project.getWorkspace().getName(),
                                project.getNamespace(),
                                branch,
                                generation.getCollectionName(),
                                generation.getRevision(),
                                generation.getManifestDigest());
                if (!outcome.successful()) {
                    successful = false;
                    stopRemaining = outcome.shouldStopRemainingTargets();
                    log.warn(
                            "Failed to delete repository-index generation for "
                                    + "project={}, branch={}, target={}: status={} detail={}",
                            project.getId(),
                            branch,
                            outcome.targetLabel(),
                            outcome.statusCode() != null
                                    ? outcome.statusCode() : outcome.failure(),
                            outcome.detail());
                    if (stopRemaining) {
                        break;
                    }
                }
            }

            if (!successful) {
                return new BranchDeletionResult(false, stopRemaining);
            }
            if (tracked.isPresent()) {
                branchIndexRepository.deleteByProjectIdAndBranchName(
                        project.getId(), branch);
            }
            emitEvent(events, Map.of(
                    "type", "success",
                    "message", "Deleted repository index for branch '"
                            + branch + "'"));
            return new BranchDeletionResult(true, false);
        } catch (Exception failure) {
            log.warn("Failed to delete repository index for project={}, branch={}: {}",
                    project.getId(), branch, failure.getMessage());
            return new BranchDeletionResult(false, true);
        }
    }

    @Override
    public Map<String, Object> cleanupStaleBranches(
            Project project,
            Set<String> activeBranches,
            Consumer<Map<String, Object>> events) {
        if (!isRagEnabled(project)) {
            return Map.of("status", "skipped", "reason", "rag_disabled");
        }

        Set<String> retained = new HashSet<>(activeBranches);
        retained.add(getBaseBranch(project));
        List<String> stale = branchIndexRepository
                .findBranchNamesByProjectId(project.getId())
                .stream()
                .filter(branch -> !retained.contains(branch))
                .toList();
        List<String> deleted = new ArrayList<>();
        List<String> failed = new ArrayList<>();
        for (String branch : stale) {
            BranchDeletionResult outcome = deleteBranchIndexWithOutcome(
                    project, branch, events);
            if (outcome.successful()) {
                deleted.add(branch);
            } else {
                failed.add(branch);
                if (outcome.stopRemaining()) {
                    break;
                }
            }
        }
        return Map.of(
                "status", "success",
                "deleted_branches", deleted,
                "failed_branches", failed,
                "total_deleted", deleted.size());
    }

    private RagBranchIndexKind indexKind(Project project, String branch) {
        return branch.equals(getBaseBranch(project))
                ? RagBranchIndexKind.PRIMARY
                : RagBranchIndexKind.DURABLE;
    }

    private static VcsRepoBinding requireBinding(Project project) {
        VcsRepoBinding binding = project.getVcsRepoBinding();
        if (binding == null
                || binding.getVcsConnection() == null
                || binding.getExternalNamespace() == null
                || binding.getExternalNamespace().isBlank()
                || binding.getExternalRepoSlug() == null
                || binding.getExternalRepoSlug().isBlank()) {
            throw new IllegalStateException(
                    "Project has no complete VcsRepoBinding configured");
        }
        return binding;
    }

    private static String normalizeRequired(String value, String field) {
        if (value == null || value.isBlank()) {
            throw new IllegalArgumentException(field + " is required");
        }
        return value.trim();
    }

    private static List<String> listOrEmpty(List<String> values) {
        return values != null ? values : List.of();
    }

    private static int number(Object value) {
        return value instanceof Number number ? number.intValue() : 0;
    }

    private static void emitEvent(
            Consumer<Map<String, Object>> events,
            Map<String, Object> event) {
        if (events == null) {
            return;
        }
        try {
            events.accept(event);
        } catch (RuntimeException observerFailure) {
            log.debug("Repository-index observer rejected event state={}: {}",
                    event.get("state"), observerFailure.getMessage());
        }
    }

    private record BranchDeletionResult(
            boolean successful,
            boolean stopRemaining) {
    }
}
