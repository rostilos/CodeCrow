package org.rostilos.codecrow.analysisengine.service.branch;

import org.junit.jupiter.api.BeforeEach;
import org.junit.jupiter.api.Test;
import org.junit.jupiter.api.extension.ExtendWith;
import org.mockito.Mock;
import org.mockito.junit.jupiter.MockitoExtension;
import org.rostilos.codecrow.core.model.job.Job;
import org.rostilos.codecrow.core.model.job.JobStatus;
import org.rostilos.codecrow.core.model.job.JobType;
import org.rostilos.codecrow.core.persistence.repository.job.JobRepository;
import org.springframework.test.util.ReflectionTestUtils;

import java.util.Map;
import java.util.Optional;
import java.util.function.Consumer;

import static org.assertj.core.api.Assertions.assertThat;
import static org.mockito.Mockito.inOrder;
import static org.mockito.Mockito.mock;
import static org.mockito.Mockito.times;
import static org.mockito.Mockito.verify;
import static org.mockito.Mockito.when;

@ExtendWith(MockitoExtension.class)
class BranchAnalysisGateServiceTest {

    @Mock
    private JobRepository jobRepository;

    private BranchAnalysisGateService service;

    @BeforeEach
    void setUp() {
        service = new BranchAnalysisGateService(jobRepository);
        ReflectionTestUtils.setField(service, "pollIntervalMillis", 0L);
        ReflectionTestUtils.setField(service, "waitTimeoutMinutes", 1L);
    }

    @Test
    void olderBranchJobsAreSupersededByTheNewestJob() {
        when(jobRepository.existsNewerBranchAnalysisJob(1L, "main", 101L))
                .thenReturn(true);

        BranchAnalysisGateService.GateResult result = service.awaitTurn(
                1L, "main", 101L, 41L, event -> { });

        assertThat(result).isEqualTo(BranchAnalysisGateService.GateResult.SUPERSEDED);
        verify(jobRepository, times(0)).existsActivePrAnalysisJob(1L, "main");
    }

    @Test
    void branchJobWithoutPrContextWaitsOnlyForEarlierPrJobs() {
        @SuppressWarnings("unchecked")
        Consumer<Map<String, Object>> consumer = mock(Consumer.class);
        when(jobRepository.existsNewerBranchAnalysisJob(1L, "main", 103L))
                .thenReturn(false);
        when(jobRepository.existsActivePrAnalysisJobBefore(1L, "main", 103L))
                .thenReturn(true, true, true, false);

        BranchAnalysisGateService.GateResult result = service.awaitTurn(
                1L, "main", 103L, null, consumer);

        assertThat(result).isEqualTo(BranchAnalysisGateService.GateResult.READY);
        verify(jobRepository, times(4)).existsActivePrAnalysisJobBefore(1L, "main", 103L);
        verify(consumer, times(3)).accept(org.mockito.ArgumentMatchers.argThat(
                event -> "pr_analysis_wait".equals(event.get("type"))));

        var ordered = inOrder(jobRepository);
        ordered.verify(jobRepository).existsNewerBranchAnalysisJob(1L, "main", 103L);
        ordered.verify(jobRepository).existsActivePrAnalysisJobBefore(1L, "main", 103L);
        ordered.verify(jobRepository).existsNewerBranchAnalysisJob(1L, "main", 103L);
        ordered.verify(jobRepository).existsActivePrAnalysisJobBefore(1L, "main", 103L);
        ordered.verify(jobRepository).existsNewerBranchAnalysisJob(1L, "main", 103L);
        ordered.verify(jobRepository).existsActivePrAnalysisJobBefore(1L, "main", 103L);
        ordered.verify(jobRepository).existsNewerBranchAnalysisJob(1L, "main", 103L);
        ordered.verify(jobRepository).existsActivePrAnalysisJobBefore(1L, "main", 103L);
    }

    @Test
    void waitingBranchJobStopsWhenANewerMergeArrives() {
        when(jobRepository.existsNewerBranchAnalysisJob(1L, "main", 101L))
                .thenReturn(false, true);
        when(jobRepository.existsActivePrAnalysisJobBefore(1L, "main", 101L))
                .thenReturn(true);

        BranchAnalysisGateService.GateResult result = service.awaitTurn(
                1L, "main", 101L, null, event -> { });

        assertThat(result).isEqualTo(BranchAnalysisGateService.GateResult.SUPERSEDED);
        verify(jobRepository, times(1)).existsActivePrAnalysisJobBefore(1L, "main", 101L);
    }

    @Test
    void mergeWaitsOnlyForNewestAttemptOfItsOwnPr() {
        @SuppressWarnings("unchecked")
        Consumer<Map<String, Object>> consumer = mock(Consumer.class);
        Job running = job(JobStatus.RUNNING);
        Job completed = job(JobStatus.COMPLETED);

        when(jobRepository.existsNewerBranchAnalysisJob(1L, "main", 103L))
                .thenReturn(false);
        when(jobRepository
                .findFirstByProjectIdAndBranchNameAndJobTypeAndPrNumberAndIdLessThanOrderByIdDesc(
                        1L, "main", JobType.PR_ANALYSIS, 41L, 103L))
                .thenReturn(Optional.of(running), Optional.of(completed));

        BranchAnalysisGateService.GateResult result = service.awaitTurn(
                1L, "main", 103L, 41L, consumer);

        assertThat(result).isEqualTo(BranchAnalysisGateService.GateResult.READY);
        verify(consumer).accept(org.mockito.ArgumentMatchers.argThat(
                event -> Long.valueOf(41L).equals(event.get("prNumber"))
                        && event.get("message").toString().contains("PR #41")));
        verify(jobRepository, times(0)).existsActivePrAnalysisJobBefore(1L, "main", 103L);
    }

    @Test
    void completedNewestAttemptDoesNotLetAnOlderStaleDuplicateBlock() {
        Job completed = job(JobStatus.COMPLETED);
        when(jobRepository.existsNewerBranchAnalysisJob(1L, "main", 103L))
                .thenReturn(false);
        when(jobRepository
                .findFirstByProjectIdAndBranchNameAndJobTypeAndPrNumberAndIdLessThanOrderByIdDesc(
                        1L, "main", JobType.PR_ANALYSIS, 41L, 103L))
                .thenReturn(Optional.of(completed));

        BranchAnalysisGateService.GateResult result = service.awaitTurn(
                1L, "main", 103L, 41L, event -> { });

        assertThat(result).isEqualTo(BranchAnalysisGateService.GateResult.READY);
        verify(jobRepository, times(0)).existsActivePrAnalysisJobBefore(1L, "main", 103L);
    }

    @Test
    void processorLevelBarrierUsesLatestAttemptForTheMergedPr() {
        Job completed = job(JobStatus.COMPLETED);
        when(jobRepository.findFirstByProjectIdAndBranchNameAndJobTypeAndPrNumberOrderByIdDesc(
                1L, "main", JobType.PR_ANALYSIS, 41L))
                .thenReturn(Optional.of(completed));

        service.awaitPrAnalysis(1L, "main", 41L, event -> { });

        verify(jobRepository)
                .findFirstByProjectIdAndBranchNameAndJobTypeAndPrNumberOrderByIdDesc(
                        1L, "main", JobType.PR_ANALYSIS, 41L);
        verify(jobRepository, times(0)).existsActivePrAnalysisJob(1L, "main");
    }

    private static Job job(JobStatus status) {
        Job job = new Job();
        job.setJobType(JobType.PR_ANALYSIS);
        job.setStatus(status);
        return job;
    }
}
