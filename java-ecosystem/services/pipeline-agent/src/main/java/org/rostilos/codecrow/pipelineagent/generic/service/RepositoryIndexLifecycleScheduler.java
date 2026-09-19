package org.rostilos.codecrow.pipelineagent.generic.service;

import org.rostilos.codecrow.analysisapi.rag.RagOperationsService;
import org.rostilos.codecrow.core.model.job.Job;
import org.rostilos.codecrow.core.model.job.JobStatus;
import org.rostilos.codecrow.core.model.job.JobType;
import org.rostilos.codecrow.core.model.job.JobTriggerSource;
import org.rostilos.codecrow.core.model.project.Project;
import org.rostilos.codecrow.core.model.vcs.VcsRepoInfo;
import org.rostilos.codecrow.core.persistence.repository.project.ProjectRepository;
import org.rostilos.codecrow.core.persistence.repository.rag.RagBranchIndexRepository;
import org.rostilos.codecrow.core.service.JobService;
import org.rostilos.codecrow.core.service.RepositoryIndexJobQueueService;
import org.rostilos.codecrow.ragengine.service.RagRepresentationIdentityService;
import org.rostilos.codecrow.vcsclient.VcsClient;
import org.rostilos.codecrow.vcsclient.VcsClientProvider;
import org.slf4j.Logger;
import org.slf4j.LoggerFactory;
import org.springframework.beans.factory.annotation.Qualifier;
import org.springframework.beans.factory.annotation.Value;
import org.springframework.data.domain.PageRequest;
import org.springframework.scheduling.annotation.Scheduled;
import org.springframework.stereotype.Service;

import java.time.OffsetDateTime;
import java.util.ArrayList;
import java.util.HashMap;
import java.util.LinkedHashSet;
import java.util.List;
import java.util.Map;
import java.util.Set;
import java.util.concurrent.Executor;
import java.util.concurrent.RejectedExecutionException;
import java.util.concurrent.atomic.AtomicLong;

/** Reconciles and dispatches the persisted repository-index queue. */
@Service
public class RepositoryIndexLifecycleScheduler {
    private static final Logger log = LoggerFactory.getLogger(
            RepositoryIndexLifecycleScheduler.class);

    private final ProjectRepository projectRepository;
    private final RagBranchIndexRepository branchIndexRepository;
    private final RepositoryIndexJobQueueService queueService;
    private final JobService jobService;
    private final RagOperationsService ragOperationsService;
    private final RagRepresentationIdentityService representationIdentityService;
    private final VcsClientProvider vcsClientProvider;
    private final Executor buildExecutor;
    private final int reconcileBatchSize;
    private final int reconcileMaxPages;
    private final int dispatchBatchSize;
    private final long queuedStaleMinutes;
    private final long failedRetryMinutes;
    private final AtomicLong projectCursor = new AtomicLong(0L);

    public RepositoryIndexLifecycleScheduler(
            ProjectRepository projectRepository,
            RagBranchIndexRepository branchIndexRepository,
            RepositoryIndexJobQueueService queueService,
            JobService jobService,
            RagOperationsService ragOperationsService,
            RagRepresentationIdentityService representationIdentityService,
            VcsClientProvider vcsClientProvider,
            @Qualifier("branchIndexBuildExecutor") Executor buildExecutor,
            @Value("${codecrow.rag.lifecycle.reconcile-batch-size:100}")
            int reconcileBatchSize,
            @Value("${codecrow.rag.lifecycle.reconcile-max-pages:20}")
            int reconcileMaxPages,
            @Value("${codecrow.rag.lifecycle.dispatch-batch-size:10}")
            int dispatchBatchSize,
            @Value("${codecrow.rag.lifecycle.queued-stale-minutes:30}")
            long queuedStaleMinutes,
            @Value("${codecrow.rag.lifecycle.failed-retry-minutes:60}")
            long failedRetryMinutes) {
        this.projectRepository = projectRepository;
        this.branchIndexRepository = branchIndexRepository;
        this.queueService = queueService;
        this.jobService = jobService;
        this.ragOperationsService = ragOperationsService;
        this.representationIdentityService = representationIdentityService;
        this.vcsClientProvider = vcsClientProvider;
        this.buildExecutor = buildExecutor;
        this.reconcileBatchSize = Math.max(1, reconcileBatchSize);
        this.reconcileMaxPages = Math.max(1, reconcileMaxPages);
        this.dispatchBatchSize = Math.max(1, dispatchBatchSize);
        this.queuedStaleMinutes = Math.max(5, queuedStaleMinutes);
        this.failedRetryMinutes = Math.max(1, failedRetryMinutes);
    }

    @Scheduled(
            fixedDelayString = "${codecrow.rag.lifecycle.reconcile-interval-ms:5000}",
            initialDelayString = "${codecrow.rag.lifecycle.reconcile-initial-delay-ms:5000}")
    public void reconcileRepositoryGenerations() {
        String runtimeIdentity = representationIdentityService
                .currentRuntimeIdentity().orElse(null);
        for (int page = 0; page < reconcileMaxPages; page++) {
            List<Project> projects = projectRepository
                    .findActiveRepositoryIndexCandidatesAfterId(
                            projectCursor.get(),
                            PageRequest.of(0, reconcileBatchSize));
            if (projects.isEmpty()) {
                projectCursor.set(0L);
                return;
            }
            Map<Long, List<RagBranchIndexRepository.ReconciliationCoordinates>>
                    coordinatesByProject = loadCoordinates(projects);
            for (Project project : projects) {
                projectCursor.set(project.getId());
                reconcileProject(
                        project,
                        coordinatesByProject.getOrDefault(project.getId(), List.of()),
                        runtimeIdentity);
            }
            if (projects.size() < reconcileBatchSize) {
                projectCursor.set(0L);
                return;
            }
        }
    }

    private Map<Long, List<RagBranchIndexRepository.ReconciliationCoordinates>>
            loadCoordinates(List<Project> projects) {
        List<Long> projectIds = projects.stream().map(Project::getId).toList();
        Map<Long, List<RagBranchIndexRepository.ReconciliationCoordinates>> grouped =
                new HashMap<>();
        for (var coordinates : branchIndexRepository
                .findReconciliationCoordinatesByProjectIds(projectIds)) {
            grouped.computeIfAbsent(coordinates.getProjectId(), ignored -> new ArrayList<>())
                    .add(coordinates);
        }
        return grouped;
    }

    private void reconcileProject(
            Project project,
            List<RagBranchIndexRepository.ReconciliationCoordinates> observed,
            String runtimeIdentity) {
        try {
            if (!ragOperationsService.isRagEnabled(project)
                    || project.getVcsRepoBinding() == null
                    || project.getVcsRepoBinding().getVcsConnection() == null) {
                return;
            }
            String primary = ragOperationsService.getBaseBranch(project);
            String desiredFingerprint = runtimeIdentity != null
                    ? representationIdentityService.projectFingerprint(
                            runtimeIdentity, project)
                    : null;
            Map<String, RagBranchIndexRepository.ReconciliationCoordinates> byBranch =
                    new HashMap<>();
            for (var coordinates : observed) {
                byBranch.put(coordinates.getBranchName(), coordinates);
            }
            Set<String> branches = new LinkedHashSet<>();
            branches.add(primary);
            observed.stream()
                    .map(RagBranchIndexRepository.ReconciliationCoordinates::getBranchName)
                    .filter(branch -> !branch.equals(primary))
                    .filter(branch -> ragOperationsService.shouldHaveBranchIndex(
                            project, branch))
                    .forEach(branches::add);

            for (String branch : branches) {
                var current = byBranch.get(branch);
                String requestedRevision = current != null
                        ? current.getDesiredCommitHash() : null;
                String reason = reconciliationReason(
                        current, requestedRevision, desiredFingerprint);
                if (reason != null && retryCooldownElapsed(current)) {
                    queueService.enqueue(
                            project,
                            branch,
                            requestedRevision,
                            JobTriggerSource.SCHEDULED,
                            reason);
                }
            }
        } catch (Exception failure) {
            log.info(
                    "Repository-index reconciliation deferred for project={}: {}",
                    project.getId(), failure.getMessage());
        }
    }

    private String reconciliationReason(
            RagBranchIndexRepository.ReconciliationCoordinates current,
            String requestedRevision,
            String desiredFingerprint) {
        if (current == null) {
            return "scheduled reconciliation found no repository-index registry "
                    + "entry for this managed branch";
        }
        if (current.getActiveRevision() == null) {
            return "scheduled reconciliation found no active generation for this "
                    + "managed branch";
        }
        if (requestedRevision != null
                && !requestedRevision.equals(current.getActiveRevision())) {
            return "scheduled reconciliation found desired revision "
                    + requestedRevision + " but the active generation is "
                    + current.getActiveRevision();
        }
        if (desiredFingerprint != null
                && !desiredFingerprint.equals(
                        current.getActiveRepresentationFingerprint())) {
            return "scheduled reconciliation detected a repository-index "
                    + "representation change";
        }
        // A failed refresh can leave a completely compatible active generation
        // behind. Error text is diagnostic state, not by itself a reason to
        // rebuild the unchanged branch forever.
        return null;
    }

    private boolean retryCooldownElapsed(
            RagBranchIndexRepository.ReconciliationCoordinates current) {
        if (current == null || current.getErrorMessage() == null
                || current.getErrorMessage().isBlank()
                || current.getLastFailedAt() == null) {
            return true;
        }
        return !current.getLastFailedAt().isAfter(
                OffsetDateTime.now().minusMinutes(failedRetryMinutes));
    }

    @Scheduled(
            fixedDelayString = "${codecrow.rag.lifecycle.dispatch-interval-ms:5000}",
            initialDelayString = "${codecrow.rag.lifecycle.dispatch-initial-delay-ms:5000}")
    public void dispatchPersistedJobs() {
        if (!ragOperationsService.isRagPipelineHealthy()) {
            return;
        }
        OffsetDateTime queuedBefore = OffsetDateTime.now()
                .minusMinutes(queuedStaleMinutes);
        for (Job candidate : queueService.findDispatchCandidates(
                queuedBefore, dispatchBatchSize)) {
            try {
                // Capacity owns head resolution as well as the expensive
                // build. A saturated executor therefore causes neither a VCS
                // request nor an ephemeral claim; the DB row stays durable.
                buildExecutor.execute(() -> resolveClaimAndExecute(
                        candidate.getId(), queuedBefore));
            } catch (RejectedExecutionException saturated) {
                log.debug(
                        "Repository-index capacity is full; job {} remains persisted",
                        candidate.getId());
                break;
            }
        }
    }

    private void resolveClaimAndExecute(long jobId, OffsetDateTime queuedBefore) {
        Job candidate = jobService.findById(jobId).orElse(null);
        if (candidate == null
                || candidate.getJobType() != JobType.REPOSITORY_INDEX_BUILD) {
            return;
        }
        try {
            String resolvedRevision = resolveDispatchRevision(candidate);
            OffsetDateTime claimedAt = OffsetDateTime.now();
            if (!queueService.claim(
                    jobId,
                    candidate.getCommitHash(),
                    resolvedRevision,
                    queuedBefore,
                    claimedAt)) {
                return;
            }
            Job claimed = jobService.findById(jobId).orElse(candidate);
            try {
                jobService.info(
                        claimed,
                        "claimed",
                        "Repository-index capacity acquired; provider branch HEAD "
                                + "resolved to " + resolvedRevision);
            } catch (Exception diagnosticFailure) {
                log.debug(
                        "Could not persist repository-index claim diagnostic for job {}",
                        jobId,
                        diagnosticFailure);
            }
            executeClaimed(jobId);
        } catch (Exception failure) {
            // This is a bounded check only for work already present in the
            // durable queue. Leave it dispatchable and expose why it was
            // deferred; no project-wide provider polling is introduced.
            jobService.warn(
                    candidate,
                    "repository_index_revision_retry",
                    "Repository-index branch-head reconciliation will retry: "
                            + diagnostic(failure));
        }
    }

    private void executeClaimed(long jobId) {
        Job job = jobService.findById(jobId).orElse(null);
        if (job == null
                || job.getJobType() != JobType.REPOSITORY_INDEX_BUILD
                || job.getStatus() != JobStatus.QUEUED) {
            return;
        }
        try {
            Project project = projectRepository.findByIdWithFullDetails(
                    job.getProject().getId()).orElse(null);
            if (project == null) {
                jobService.failJob(job, "Project no longer exists");
                return;
            }
            String branch = job.getBranchName();
            if (branch == null || branch.isBlank()) {
                branch = ragOperationsService.getBaseBranch(project);
            }
            String revision = normalize(job.getCommitHash());
            if (revision == null) {
                jobService.failJob(
                        job,
                        "Repository-index claim has no resolved branch revision");
                return;
            }
            ragOperationsService.executeQueuedBranchGeneration(
                    project, branch, revision, job);
        } catch (Exception failure) {
            // Failures after the immutable claim remain QUEUED so stale-claim
            // recovery can retry them without losing the durable request.
            Job current = jobService.findById(jobId).orElse(job);
            if (current.getStatus() == JobStatus.QUEUED) {
                jobService.warn(
                        current,
                        "repository_index_retry",
                        "Repository-index dispatch will retry: "
                                + diagnostic(failure));
            } else if (!current.isTerminal()) {
                jobService.failJob(current, diagnostic(failure));
            }
        }
    }

    /**
     * Resolve the authoritative branch head before the atomic claim. This
     * prevents an out-of-order webhook from regressing desired or active state:
     * arrival order never decides which commit is built.
     */
    private String resolveDispatchRevision(Job candidate) throws Exception {
        if (candidate == null || candidate.getProject() == null
                || candidate.getProject().getId() == null) {
            throw new IllegalStateException(
                    "Repository-index job has no persisted project");
        }
        Project project = projectRepository.findByIdWithFullDetails(
                candidate.getProject().getId()).orElseThrow(() ->
                        new IllegalStateException("Project no longer exists"));
        String branch = normalize(candidate.getBranchName());
        if (branch == null) {
            branch = ragOperationsService.getBaseBranch(project);
        }
        return resolveCurrentRevision(project, branch);
    }

    private String resolveCurrentRevision(Project project, String branch)
            throws Exception {
        VcsRepoInfo repository = project.getEffectiveVcsRepoInfo();
        if (repository == null || repository.getVcsConnection() == null) {
            throw new IllegalStateException("Project has no VCS repository binding");
        }
        VcsClient client = vcsClientProvider.getClient(
                repository.getVcsConnection());
        String revision = client.getLatestCommitHash(
                repository.getRepoWorkspace(),
                repository.getRepoSlug(),
                branch);
        if (revision == null || revision.isBlank()) {
            throw new IllegalStateException(
                    "Branch '" + branch + "' has no resolvable revision");
        }
        return revision.trim();
    }

    private static String diagnostic(Throwable failure) {
        return failure.getMessage() != null
                ? failure.getMessage()
                : failure.getClass().getSimpleName();
    }

    private static String normalize(String value) {
        return value == null || value.isBlank() ? null : value.trim();
    }
}
