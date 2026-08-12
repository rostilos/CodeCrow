package org.rostilos.codecrow.ragengine.branch;

import ch.qos.logback.classic.Level;
import ch.qos.logback.classic.Logger;
import ch.qos.logback.classic.spi.ILoggingEvent;
import ch.qos.logback.core.read.ListAppender;
import org.junit.jupiter.api.BeforeEach;
import org.junit.jupiter.api.Test;
import org.rostilos.codecrow.core.model.job.Job;
import org.rostilos.codecrow.core.persistence.repository.job.JobRepository;
import org.rostilos.codecrow.core.service.JobService;
import org.rostilos.codecrow.ragengine.service.RagIndexTrackingService;

import java.time.OffsetDateTime;
import java.util.List;
import java.util.Optional;

import static org.assertj.core.api.Assertions.assertThat;
import static org.mockito.ArgumentMatchers.*;
import static org.mockito.Mockito.*;

class LegacyRagJobRecoveryServiceTest {
    private JobService jobs;
    private RagIndexTrackingService tracking;
    private LegacyRagJobRecoveryService recovery;
    private JobRepository.LegacyRagJobRecoveryCoordinates coordinates;

    @BeforeEach
    void setUp() {
        jobs = mock(JobService.class);
        tracking = mock(RagIndexTrackingService.class);
        recovery = new LegacyRagJobRecoveryService(
                jobs, tracking, 120, 100);
        coordinates = mock(
                JobRepository.LegacyRagJobRecoveryCoordinates.class);
        when(coordinates.getJobId()).thenReturn(91L);
        when(coordinates.getProjectId()).thenReturn(42L);
        when(coordinates.getBranchName()).thenReturn("main");
        when(coordinates.getCommitHash()).thenReturn("commit-a");
    }

    @Test
    void staleProducerIsAtomicallyFailedAndItsOwnedStatusIsRestored() {
        Job job = mock(Job.class);
        when(jobs.findAbandonedLegacyRagJobs(any(OffsetDateTime.class), eq(100)))
                .thenReturn(List.of(coordinates));
        when(jobs.failAbandonedLegacyRagJob(
                eq(91L), any(OffsetDateTime.class), anyString()))
                .thenReturn(true);
        when(jobs.findById(91L)).thenReturn(Optional.of(job));

        recovery.failAbandonedJobs();

        verify(jobs).failAbandonedLegacyRagJob(
                eq(91L), any(OffsetDateTime.class), contains("stopped heartbeating"));
        verify(jobs).recordExternallyFailedJob(
                eq(job), eq("rag_recovery"),
                contains("last completed checkpoint was preserved"));
        verify(tracking).recoverAbandonedIncrementalUpdate(
                eq(42L), eq(91L), contains("stopped heartbeating"));
    }

    @Test
    void heartbeatWinningTheCasPreservesTheLiveProducer() {
        when(jobs.findAbandonedLegacyRagJobs(any(OffsetDateTime.class), eq(100)))
                .thenReturn(List.of(coordinates));
        when(jobs.failAbandonedLegacyRagJob(
                eq(91L), any(OffsetDateTime.class), anyString()))
                .thenReturn(false);

        recovery.failAbandonedJobs();

        verify(jobs, never()).findById(anyLong());
        verifyNoInteractions(tracking);
    }

    @Test
    void statusOwnedByANewerJobIsPreserved() {
        when(jobs.findAbandonedLegacyRagJobs(any(OffsetDateTime.class), eq(100)))
                .thenReturn(List.of(coordinates));
        when(jobs.failAbandonedLegacyRagJob(
                eq(91L), any(OffsetDateTime.class), anyString()))
                .thenReturn(true);
        when(tracking.recoverAbandonedIncrementalUpdate(
                eq(42L), eq(91L), anyString())).thenReturn(false);

        recovery.failAbandonedJobs();

        verify(tracking).recoverAbandonedIncrementalUpdate(
                eq(42L), eq(91L), anyString());
    }

    @Test
    void failedJobProjectionDriftIsRetriedOnLaterScan() {
        when(coordinates.getErrorMessage()).thenReturn("prior producer failure");
        when(jobs.findFailedLegacyRagJobsWithActiveStatus(100))
                .thenReturn(List.of(coordinates));

        recovery.failAbandonedJobs();

        verify(tracking).recoverAbandonedIncrementalUpdate(
                42L, 91L, "prior producer failure");
        verify(jobs, never()).failAbandonedLegacyRagJob(
                anyLong(), any(), anyString());
    }

    @Test
    void scanOutageLogsOneTransitionThenDebugAndOneRecovery() {
        Logger logger = (Logger) org.slf4j.LoggerFactory.getLogger(
                LegacyRagJobRecoveryService.class);
        Level previousLevel = logger.getLevel();
        logger.setLevel(Level.DEBUG);
        ListAppender<ILoggingEvent> logs = new ListAppender<>();
        logs.start();
        logger.addAppender(logs);
        try {
            when(jobs.findAbandonedLegacyRagJobs(
                    any(OffsetDateTime.class), eq(100)))
                    .thenThrow(new IllegalStateException("database unavailable"))
                    .thenThrow(new IllegalStateException("database unavailable"))
                    .thenReturn(List.of());

            recovery.failAbandonedJobs();
            recovery.failAbandonedJobs();
            recovery.failAbandonedJobs();

            long warnings = logs.list.stream()
                    .filter(event -> event.getLevel() == Level.WARN)
                    .filter(event -> event.getFormattedMessage()
                            .contains("recovery degraded"))
                    .count();
            long repeats = logs.list.stream()
                    .filter(event -> event.getLevel() == Level.DEBUG)
                    .filter(event -> event.getFormattedMessage()
                            .contains("remains degraded"))
                    .count();
            long recoveries = logs.list.stream()
                    .filter(event -> event.getLevel() == Level.INFO)
                    .filter(event -> event.getFormattedMessage()
                            .contains("recovery scan recovered"))
                    .count();
            assertThat(warnings).isEqualTo(1);
            assertThat(repeats).isEqualTo(1);
            assertThat(recoveries).isEqualTo(1);
        } finally {
            logger.detachAppender(logs);
            logs.stop();
            logger.setLevel(previousLevel);
        }
    }
}
