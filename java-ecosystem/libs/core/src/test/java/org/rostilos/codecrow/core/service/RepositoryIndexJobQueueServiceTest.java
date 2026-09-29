package org.rostilos.codecrow.core.service;

import org.junit.jupiter.api.BeforeEach;
import org.junit.jupiter.api.Test;
import org.junit.jupiter.api.extension.ExtendWith;
import org.mockito.Mock;
import org.mockito.junit.jupiter.MockitoExtension;
import org.rostilos.codecrow.core.model.job.Job;
import org.rostilos.codecrow.core.model.job.JobStatus;
import org.rostilos.codecrow.core.model.job.JobTriggerSource;
import org.rostilos.codecrow.core.model.project.Project;
import org.rostilos.codecrow.core.persistence.repository.job.JobRepository;
import org.rostilos.codecrow.core.persistence.repository.project.ProjectRepository;
import org.springframework.data.domain.Pageable;
import org.springframework.data.jpa.repository.Query;
import org.springframework.test.util.ReflectionTestUtils;

import java.time.OffsetDateTime;
import java.util.List;
import java.util.Optional;

import static org.assertj.core.api.Assertions.assertThat;
import static org.mockito.Mockito.*;

@ExtendWith(MockitoExtension.class)
class RepositoryIndexJobQueueServiceTest {

    @Mock private ProjectRepository projectRepository;
    @Mock private JobRepository jobRepository;
    @Mock private JobService jobService;
    @Mock private Project project;

    private RepositoryIndexJobQueueService queue;

    @BeforeEach
    void setUp() {
        queue = new RepositoryIndexJobQueueService(
                projectRepository, jobRepository, jobService);
        lenient().when(project.getId()).thenReturn(42L);
        lenient().when(projectRepository.findByIdForUpdate(42L))
                .thenReturn(Optional.of(project));
    }

    @Test
    void exactActiveRevisionIsDeduplicated() {
        Job active = job(JobStatus.RUNNING, "revision-a");
        when(jobRepository.findActiveRepositoryIndexJobs(42L, "main"))
                .thenReturn(List.of(active));

        Job accepted = queue.enqueue(
                project, "main", " revision-a ", JobTriggerSource.WEBHOOK);

        assertThat(accepted).isSameAs(active);
        verifyNoInteractions(jobService);
    }

    @Test
    void eventPinsAnUnclaimedBootstrapJobInsteadOfLosingItsRevision() {
        Job pending = job(JobStatus.PENDING, null);
        when(jobRepository.findActiveRepositoryIndexJobs(42L, "release/1.x"))
                .thenReturn(List.of(pending));
        when(jobRepository.updatePendingRepositoryIndexRevision(
                eq(91L), eq("revision-b"), any(OffsetDateTime.class)))
                .thenReturn(1);
        Job updated = job(JobStatus.PENDING, "revision-b");
        when(jobRepository.findById(91L)).thenReturn(Optional.of(updated));

        Job accepted = queue.enqueue(
                project,
                "release/1.x",
                "revision-b",
                JobTriggerSource.WEBHOOK);

        assertThat(accepted).isSameAs(updated);
        verify(jobRepository).updatePendingRepositoryIndexRevision(
                eq(91L), eq("revision-b"), any(OffsetDateTime.class));
        verifyNoInteractions(jobService);
    }

    @Test
    void claimWinningPendingUpdateRacePreservesNewerRequestAsSuccessor() {
        Job candidate = job(JobStatus.PENDING, "revision-a");
        Job successor = job(JobStatus.PENDING, "revision-b");
        when(jobRepository.findActiveRepositoryIndexJobs(42L, "main"))
                .thenReturn(List.of(candidate));
        when(jobRepository.updatePendingRepositoryIndexRevision(
                eq(91L), eq("revision-b"), any(OffsetDateTime.class)))
                .thenReturn(0);
        when(jobService.createRepositoryIndexBuildJob(
                project,
                JobTriggerSource.WEBHOOK,
                null,
                "main",
                "revision-b"))
                .thenReturn(successor);

        Job accepted = queue.enqueue(
                project, "main", "revision-b", JobTriggerSource.WEBHOOK);

        assertThat(accepted).isSameAs(successor);
        verify(jobService).createRepositoryIndexBuildJob(
                project,
                JobTriggerSource.WEBHOOK,
                null,
                "main",
                "revision-b");
    }

    @Test
    void manualRebuildPromotesUnclaimedAutomaticSuccessor() {
        Job pending = job(JobStatus.PENDING, "revision-a");
        Job promoted = job(JobStatus.PENDING, "revision-a");
        promoted.setTriggerSource(JobTriggerSource.UI);
        when(jobRepository.findActiveRepositoryIndexJobs(42L, "main"))
                .thenReturn(List.of(pending));
        when(jobRepository.promotePendingRepositoryIndexJobToOperator(
                eq(91L), any(OffsetDateTime.class))).thenReturn(1);
        when(jobRepository.findById(91L)).thenReturn(Optional.of(promoted));

        Job accepted = queue.enqueue(
                project, "main", null, JobTriggerSource.UI);

        assertThat(accepted).isSameAs(promoted);
        assertThat(accepted.getTriggerSource()).isEqualTo(JobTriggerSource.UI);
        verifyNoInteractions(jobService);
    }

    @Test
    void manualRebuildBehindClaimedAutomaticWorkCreatesOperatorSuccessor() {
        Job queued = job(JobStatus.QUEUED, "revision-a");
        Job operator = job(JobStatus.PENDING, null);
        operator.setTriggerSource(JobTriggerSource.UI);
        when(jobRepository.findActiveRepositoryIndexJobs(42L, "main"))
                .thenReturn(List.of(queued));
        when(jobService.createRepositoryIndexBuildJob(
                project,
                JobTriggerSource.UI,
                null,
                "main",
                null))
                .thenReturn(operator);

        Job accepted = queue.enqueue(
                project, "main", null, JobTriggerSource.UI);

        assertThat(accepted).isSameAs(operator);
        verify(jobService).createRepositoryIndexBuildJob(
                project,
                JobTriggerSource.UI,
                null,
                "main",
                null);
    }

    @Test
    void atomicClaimRequiresCandidateRevisionAndPinsResolvedHead() throws Exception {
        OffsetDateTime cutoff = OffsetDateTime.now().minusMinutes(30);
        OffsetDateTime claimedAt = OffsetDateTime.now();
        when(jobRepository.claimRepositoryIndexJob(
                91L, "revision-a", "revision-b", cutoff, claimedAt))
                .thenReturn(1);

        assertThat(queue.claim(
                91L, "revision-a", "revision-b", cutoff, claimedAt)).isTrue();

        Query query = JobRepository.class.getMethod(
                        "claimRepositoryIndexJob",
                        Long.class,
                        String.class,
                        String.class,
                        OffsetDateTime.class,
                        OffsetDateTime.class)
                .getAnnotation(Query.class);
        assertThat(query).isNotNull();
        assertThat(query.value())
                .contains("j.currentStep = 'Repository-index capacity acquired'");
    }

    @Test
    void dispatchQueryRotatesTouchedFailuresAndSerializesBranchSuccessors()
            throws Exception {
        Query query = JobRepository.class.getMethod(
                        "findRepositoryIndexDispatchCandidates",
                        OffsetDateTime.class,
                        Pageable.class)
                .getAnnotation(Query.class);

        assertThat(query).isNotNull();
        assertThat(query.value())
                .contains("ORDER BY j.updatedAt ASC, j.id ASC")
                .contains("AND NOT EXISTS (SELECT older.id FROM Job older")
                .contains("older.project.id = j.project.id")
                .contains("older.branchName = j.branchName")
                .contains("older.id < j.id")
                .contains("JobStatus.RUNNING")
                .doesNotContain("ORDER BY j.createdAt");
    }

    @Test
    void newerRevisionBehindClaimedWorkCreatesDurableSuccessor() {
        Job running = job(JobStatus.RUNNING, "revision-a");
        Job successor = job(JobStatus.PENDING, "revision-b");
        when(jobRepository.findActiveRepositoryIndexJobs(42L, "main"))
                .thenReturn(List.of(running));
        when(jobService.createRepositoryIndexBuildJob(
                project,
                JobTriggerSource.WEBHOOK,
                null,
                "main",
                "revision-b"))
                .thenReturn(successor);

        Job accepted = queue.enqueue(
                project, "main", "revision-b", JobTriggerSource.WEBHOOK);

        assertThat(accepted).isSameAs(successor);
    }

    @Test
    void reconciliationReasonIsPreservedOnNewPersistentWork() {
        Job pending = job(JobStatus.PENDING, "revision-a");
        when(jobRepository.findActiveRepositoryIndexJobs(42L, "main"))
                .thenReturn(List.of());
        when(jobService.createRepositoryIndexBuildJob(
                project,
                JobTriggerSource.SCHEDULED,
                null,
                "main",
                "revision-a",
                "representation changed"))
                .thenReturn(pending);

        Job accepted = queue.enqueue(
                project,
                "main",
                "revision-a",
                JobTriggerSource.SCHEDULED,
                "representation changed");

        assertThat(accepted).isSameAs(pending);
        verify(jobService).createRepositoryIndexBuildJob(
                project,
                JobTriggerSource.SCHEDULED,
                null,
                "main",
                "revision-a",
                "representation changed");
    }

    private static Job job(JobStatus status, String revision) {
        Job job = new Job();
        ReflectionTestUtils.setField(job, "id", 91L);
        job.setStatus(status);
        job.setCommitHash(revision);
        job.setTriggerSource(JobTriggerSource.WEBHOOK);
        return job;
    }
}
