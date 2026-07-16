package org.rostilos.codecrow.analysisengine.service.branch;

import org.junit.jupiter.api.BeforeEach;
import org.junit.jupiter.api.Test;
import org.junit.jupiter.api.extension.ExtendWith;
import org.mockito.Mock;
import org.mockito.junit.jupiter.MockitoExtension;
import org.rostilos.codecrow.core.persistence.repository.job.JobRepository;
import org.springframework.test.util.ReflectionTestUtils;

import java.util.Map;
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
                1L, "main", 101L, event -> { });

        assertThat(result).isEqualTo(BranchAnalysisGateService.GateResult.SUPERSEDED);
        verify(jobRepository, times(0)).existsActivePrAnalysisJob(1L, "main");
    }

    @Test
    void newestBranchJobWaitsUntilEveryPrAnalysisForTheTargetBranchFinishes() {
        @SuppressWarnings("unchecked")
        Consumer<Map<String, Object>> consumer = mock(Consumer.class);
        when(jobRepository.existsNewerBranchAnalysisJob(1L, "main", 103L))
                .thenReturn(false);
        when(jobRepository.existsActivePrAnalysisJob(1L, "main"))
                .thenReturn(true, true, true, false);

        BranchAnalysisGateService.GateResult result = service.awaitTurn(
                1L, "main", 103L, consumer);

        assertThat(result).isEqualTo(BranchAnalysisGateService.GateResult.READY);
        verify(jobRepository, times(4)).existsActivePrAnalysisJob(1L, "main");
        verify(consumer, times(3)).accept(org.mockito.ArgumentMatchers.argThat(
                event -> "pr_analysis_wait".equals(event.get("type"))));

        var ordered = inOrder(jobRepository);
        ordered.verify(jobRepository).existsNewerBranchAnalysisJob(1L, "main", 103L);
        ordered.verify(jobRepository).existsActivePrAnalysisJob(1L, "main");
        ordered.verify(jobRepository).existsNewerBranchAnalysisJob(1L, "main", 103L);
        ordered.verify(jobRepository).existsActivePrAnalysisJob(1L, "main");
        ordered.verify(jobRepository).existsNewerBranchAnalysisJob(1L, "main", 103L);
        ordered.verify(jobRepository).existsActivePrAnalysisJob(1L, "main");
        ordered.verify(jobRepository).existsNewerBranchAnalysisJob(1L, "main", 103L);
        ordered.verify(jobRepository).existsActivePrAnalysisJob(1L, "main");
    }

    @Test
    void waitingBranchJobStopsWhenANewerMergeArrives() {
        when(jobRepository.existsNewerBranchAnalysisJob(1L, "main", 101L))
                .thenReturn(false, true);
        when(jobRepository.existsActivePrAnalysisJob(1L, "main"))
                .thenReturn(true);

        BranchAnalysisGateService.GateResult result = service.awaitTurn(
                1L, "main", 101L, event -> { });

        assertThat(result).isEqualTo(BranchAnalysisGateService.GateResult.SUPERSEDED);
        verify(jobRepository, times(1)).existsActivePrAnalysisJob(1L, "main");
    }
}
