package org.rostilos.codecrow.pipelineagent.generic.service;

import org.junit.jupiter.api.BeforeEach;
import org.junit.jupiter.api.Test;
import org.junit.jupiter.api.extension.ExtendWith;
import org.mockito.Mock;
import org.mockito.junit.jupiter.MockitoExtension;
import org.rostilos.codecrow.analysisapi.rag.RagOperationsService;
import org.rostilos.codecrow.core.model.job.Job;
import org.rostilos.codecrow.core.model.job.JobStatus;
import org.rostilos.codecrow.core.model.job.JobTriggerSource;
import org.rostilos.codecrow.core.model.job.JobType;
import org.rostilos.codecrow.core.model.project.Project;
import org.rostilos.codecrow.core.model.vcs.VcsConnection;
import org.rostilos.codecrow.core.model.vcs.VcsRepoBinding;
import org.rostilos.codecrow.core.model.vcs.VcsRepoInfo;
import org.rostilos.codecrow.core.persistence.repository.project.ProjectRepository;
import org.rostilos.codecrow.core.persistence.repository.rag.RagBranchIndexRepository;
import org.rostilos.codecrow.core.service.JobService;
import org.rostilos.codecrow.core.service.RepositoryIndexJobQueueService;
import org.rostilos.codecrow.ragengine.service.RagRepresentationIdentityService;
import org.rostilos.codecrow.vcsclient.VcsClient;
import org.rostilos.codecrow.vcsclient.VcsClientProvider;
import org.springframework.data.domain.Pageable;

import java.time.OffsetDateTime;
import java.util.List;
import java.util.Optional;
import java.util.concurrent.Executor;

import static org.mockito.ArgumentMatchers.*;
import static org.mockito.Mockito.*;

@ExtendWith(MockitoExtension.class)
class RepositoryIndexLifecycleSchedulerTest {

    @Mock private ProjectRepository projectRepository;
    @Mock private RagBranchIndexRepository branchIndexRepository;
    @Mock private RepositoryIndexJobQueueService queueService;
    @Mock private JobService jobService;
    @Mock private RagOperationsService ragOperationsService;
    @Mock private RagRepresentationIdentityService identityService;
    @Mock private VcsClientProvider vcsClientProvider;

    private RepositoryIndexLifecycleScheduler scheduler;

    @BeforeEach
    void setUp() {
        scheduler = scheduler(Runnable::run);
    }

    @Test
    void delayedOlderWebhookIsPinnedToAuthoritativeHeadBeforeClaim() throws Exception {
        Project project = mock(Project.class);
        Job delayedCandidate = mock(Job.class);
        Job claimed = mock(Job.class);
        VcsRepoInfo repository = mock(VcsRepoInfo.class);
        VcsConnection connection = mock(VcsConnection.class);
        VcsClient client = mock(VcsClient.class);
        when(delayedCandidate.getId()).thenReturn(77L);
        when(delayedCandidate.getJobType()).thenReturn(
                JobType.REPOSITORY_INDEX_BUILD);
        when(delayedCandidate.getProject()).thenReturn(project);
        when(delayedCandidate.getBranchName()).thenReturn("release/1.x");
        when(delayedCandidate.getCommitHash()).thenReturn("revision-r1");
        when(claimed.getJobType()).thenReturn(JobType.REPOSITORY_INDEX_BUILD);
        when(claimed.getStatus()).thenReturn(JobStatus.QUEUED);
        when(claimed.getProject()).thenReturn(project);
        when(claimed.getBranchName()).thenReturn("release/1.x");
        when(claimed.getCommitHash()).thenReturn("revision-r2");
        when(project.getId()).thenReturn(42L);
        when(project.getEffectiveVcsRepoInfo()).thenReturn(repository);
        when(repository.getVcsConnection()).thenReturn(connection);
        when(repository.getRepoWorkspace()).thenReturn("team");
        when(repository.getRepoSlug()).thenReturn("repo");
        when(vcsClientProvider.getClient(connection)).thenReturn(client);
        when(client.getLatestCommitHash("team", "repo", "release/1.x"))
                .thenReturn("revision-r2");
        when(ragOperationsService.isRagPipelineHealthy()).thenReturn(true);
        when(queueService.findDispatchCandidates(any(), eq(10)))
                .thenReturn(List.of(delayedCandidate));
        when(jobService.findById(77L)).thenReturn(
                Optional.of(delayedCandidate), Optional.of(claimed));
        when(queueService.claim(
                eq(77L), eq("revision-r1"), eq("revision-r2"), any(), any()))
                .thenReturn(true);
        when(projectRepository.findByIdWithFullDetails(42L))
                .thenReturn(Optional.of(project));

        scheduler.dispatchPersistedJobs();

        verify(ragOperationsService).executeQueuedBranchGeneration(
                project, "release/1.x", "revision-r2", claimed);
        verify(queueService).claim(
                eq(77L), eq("revision-r1"), eq("revision-r2"), any(), any());
    }

    @Test
    void r2ThenDelayedR1BothConvergeOnR2() throws Exception {
        Project project = mock(Project.class);
        VcsRepoInfo repository = mock(VcsRepoInfo.class);
        VcsConnection connection = mock(VcsConnection.class);
        VcsClient client = mock(VcsClient.class);
        Job r2Candidate = candidate(76L, project, "main", "revision-r2");
        Job delayedR1 = candidate(77L, project, "main", "revision-r1");
        Job claimedR2 = claimed(project, "main", "revision-r2");
        Job correctedR2 = claimed(project, "main", "revision-r2");
        when(project.getId()).thenReturn(42L);
        when(project.getEffectiveVcsRepoInfo()).thenReturn(repository);
        when(repository.getVcsConnection()).thenReturn(connection);
        when(repository.getRepoWorkspace()).thenReturn("team");
        when(repository.getRepoSlug()).thenReturn("repo");
        when(vcsClientProvider.getClient(connection)).thenReturn(client);
        when(client.getLatestCommitHash("team", "repo", "main"))
                .thenReturn("revision-r2");
        when(ragOperationsService.isRagPipelineHealthy()).thenReturn(true);
        when(queueService.findDispatchCandidates(any(), eq(10)))
                .thenReturn(List.of(r2Candidate, delayedR1));
        when(queueService.claim(
                eq(76L), eq("revision-r2"), eq("revision-r2"), any(), any()))
                .thenReturn(true);
        when(queueService.claim(
                eq(77L), eq("revision-r1"), eq("revision-r2"), any(), any()))
                .thenReturn(true);
        when(jobService.findById(76L)).thenReturn(
                Optional.of(r2Candidate), Optional.of(claimedR2));
        when(jobService.findById(77L)).thenReturn(
                Optional.of(delayedR1), Optional.of(correctedR2));
        when(projectRepository.findByIdWithFullDetails(42L))
                .thenReturn(Optional.of(project));

        scheduler.dispatchPersistedJobs();

        verify(ragOperationsService).executeQueuedBranchGeneration(
                project, "main", "revision-r2", claimedR2);
        verify(ragOperationsService).executeQueuedBranchGeneration(
                project, "main", "revision-r2", correctedR2);
    }

    @Test
    void reconciliationQueuesRepresentationMismatchButNotCompatibleActiveWithOldError() {
        Project project = eligibleProject();
        var primary = coordinates(
                42L, "main", "revision-main", "revision-main", "old-fingerprint", null);
        var observed = coordinates(
                42L, "release/1.x", "revision-release", "revision-release",
                "current-fingerprint", "archive unavailable");
        when(projectRepository.findActiveRepositoryIndexCandidatesAfterId(
                eq(0L), any(Pageable.class))).thenReturn(List.of(project));
        when(branchIndexRepository.findReconciliationCoordinatesByProjectIds(
                List.of(42L))).thenReturn(List.of(primary, observed));
        when(identityService.currentRuntimeIdentity()).thenReturn(Optional.of("runtime-a"));
        when(identityService.projectFingerprint("runtime-a", project))
                .thenReturn("current-fingerprint");
        when(ragOperationsService.shouldHaveBranchIndex(project, "release/1.x"))
                .thenReturn(true);

        scheduler.reconcileRepositoryGenerations();

        verify(queueService).enqueue(
                eq(project),
                eq("main"),
                eq("revision-main"),
                eq(JobTriggerSource.SCHEDULED),
                contains("representation change"));
        verify(queueService, never()).enqueue(
                eq(project),
                eq("release/1.x"),
                any(),
                eq(JobTriggerSource.SCHEDULED),
                any());
    }

    @Test
    void recentFailedRefreshWaitsForRetryCooldown() {
        Project project = eligibleProject();
        var primary = coordinates(
                42L, "main", "revision-main", "revision-main", "old-fingerprint",
                "temporary RAG failure");
        when(primary.getLastFailedAt()).thenReturn(OffsetDateTime.now());
        when(projectRepository.findActiveRepositoryIndexCandidatesAfterId(
                eq(0L), any(Pageable.class))).thenReturn(List.of(project));
        when(branchIndexRepository.findReconciliationCoordinatesByProjectIds(
                List.of(42L))).thenReturn(List.of(primary));
        when(identityService.currentRuntimeIdentity()).thenReturn(Optional.of("runtime-a"));
        when(identityService.projectFingerprint("runtime-a", project))
                .thenReturn("current-fingerprint");

        scheduler.reconcileRepositoryGenerations();

        verifyNoInteractions(queueService);
    }

    @Test
    void failedRefreshRetriesAfterCooldownWhenGenerationIsStillIncompatible() {
        Project project = eligibleProject();
        var primary = coordinates(
                42L, "main", "revision-main", "revision-main", "old-fingerprint",
                "temporary RAG failure");
        when(primary.getLastFailedAt()).thenReturn(
                OffsetDateTime.now().minusMinutes(61));
        when(projectRepository.findActiveRepositoryIndexCandidatesAfterId(
                eq(0L), any(Pageable.class))).thenReturn(List.of(project));
        when(branchIndexRepository.findReconciliationCoordinatesByProjectIds(
                List.of(42L))).thenReturn(List.of(primary));
        when(identityService.currentRuntimeIdentity()).thenReturn(Optional.of("runtime-a"));
        when(identityService.projectFingerprint("runtime-a", project))
                .thenReturn("current-fingerprint");

        scheduler.reconcileRepositoryGenerations();

        verify(queueService).enqueue(
                eq(project),
                eq("main"),
                eq("revision-main"),
                eq(JobTriggerSource.SCHEDULED),
                contains("representation change"));
    }

    @Test
    void missingPrimaryGenerationIsDurablyQueuedForHeadResolution() {
        Project project = eligibleProject();
        when(projectRepository.findActiveRepositoryIndexCandidatesAfterId(
                eq(0L), any(Pageable.class))).thenReturn(List.of(project));
        when(branchIndexRepository.findReconciliationCoordinatesByProjectIds(
                List.of(42L))).thenReturn(List.of());
        when(identityService.currentRuntimeIdentity()).thenReturn(Optional.empty());

        scheduler.reconcileRepositoryGenerations();

        verify(queueService).enqueue(
                eq(project),
                eq("main"),
                isNull(),
                eq(JobTriggerSource.SCHEDULED),
                contains("no repository-index registry entry"));
    }

    @Test
    void executorSaturationLeavesPersistentCandidateUnclaimed() {
        Executor saturated = command -> {
            throw new java.util.concurrent.RejectedExecutionException("full");
        };
        RepositoryIndexLifecycleScheduler saturatedScheduler = scheduler(saturated);
        Job job = mock(Job.class);
        when(job.getId()).thenReturn(77L);
        when(ragOperationsService.isRagPipelineHealthy()).thenReturn(true);
        when(queueService.findDispatchCandidates(any(), eq(10)))
                .thenReturn(List.of(job));

        saturatedScheduler.dispatchPersistedJobs();

        verify(queueService, never()).claim(anyLong(), any(), any(), any(), any());
        verifyNoInteractions(vcsClientProvider);
    }

    private RepositoryIndexLifecycleScheduler scheduler(Executor executor) {
        return new RepositoryIndexLifecycleScheduler(
                projectRepository,
                branchIndexRepository,
                queueService,
                jobService,
                ragOperationsService,
                identityService,
                vcsClientProvider,
                executor,
                100,
                20,
                10,
                30,
                60);
    }

    private Project eligibleProject() {
        Project project = mock(Project.class);
        VcsRepoBinding binding = mock(VcsRepoBinding.class);
        when(project.getId()).thenReturn(42L);
        when(project.getVcsRepoBinding()).thenReturn(binding);
        when(binding.getVcsConnection()).thenReturn(mock(
                org.rostilos.codecrow.core.model.vcs.VcsConnection.class));
        when(ragOperationsService.isRagEnabled(project)).thenReturn(true);
        when(ragOperationsService.getBaseBranch(project)).thenReturn("main");
        return project;
    }

    private static Job candidate(
            long id,
            Project project,
            String branch,
            String revision) {
        Job job = mock(Job.class);
        when(job.getId()).thenReturn(id);
        when(job.getJobType()).thenReturn(JobType.REPOSITORY_INDEX_BUILD);
        when(job.getProject()).thenReturn(project);
        when(job.getBranchName()).thenReturn(branch);
        when(job.getCommitHash()).thenReturn(revision);
        return job;
    }

    private static Job claimed(
            Project project,
            String branch,
            String revision) {
        Job job = mock(Job.class);
        when(job.getJobType()).thenReturn(JobType.REPOSITORY_INDEX_BUILD);
        when(job.getStatus()).thenReturn(JobStatus.QUEUED);
        when(job.getProject()).thenReturn(project);
        when(job.getBranchName()).thenReturn(branch);
        when(job.getCommitHash()).thenReturn(revision);
        return job;
    }

    private static RagBranchIndexRepository.ReconciliationCoordinates coordinates(
            long projectId,
            String branch,
            String desired,
            String active,
            String fingerprint,
            String error) {
        var coordinates = mock(
                RagBranchIndexRepository.ReconciliationCoordinates.class);
        when(coordinates.getProjectId()).thenReturn(projectId);
        when(coordinates.getBranchName()).thenReturn(branch);
        when(coordinates.getDesiredCommitHash()).thenReturn(desired);
        when(coordinates.getActiveRevision()).thenReturn(active);
        lenient().when(coordinates.getActiveRepresentationFingerprint())
                .thenReturn(fingerprint);
        lenient().when(coordinates.getErrorMessage()).thenReturn(error);
        return coordinates;
    }
}
