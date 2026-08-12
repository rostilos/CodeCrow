package org.rostilos.codecrow.pipelineagent.generic.service;

import ch.qos.logback.classic.Level;
import ch.qos.logback.classic.Logger;
import ch.qos.logback.classic.spi.ILoggingEvent;
import ch.qos.logback.core.read.ListAppender;
import org.junit.jupiter.api.Test;
import org.rostilos.codecrow.analysisengine.dto.request.processor.BranchProcessRequest;
import org.rostilos.codecrow.analysisengine.processor.analysis.BranchAnalysisProcessor;
import org.rostilos.codecrow.core.model.branch.BranchHealthStatus;
import org.rostilos.codecrow.core.persistence.repository.branch.BranchRepository;
import org.rostilos.codecrow.events.EventNotificationEmitter;
import org.slf4j.LoggerFactory;
import org.springframework.transaction.annotation.Transactional;

import java.util.List;
import java.util.Map;

import static org.assertj.core.api.Assertions.assertThat;
import static org.mockito.ArgumentMatchers.any;
import static org.mockito.ArgumentMatchers.notNull;
import static org.mockito.Mockito.mock;
import static org.mockito.Mockito.doAnswer;
import static org.mockito.Mockito.never;
import static org.mockito.Mockito.verify;
import static org.mockito.Mockito.when;

class BranchHealthSchedulerTest {

    @Test
    void retryRunsOutsideTransactionUsingMaterializedCandidate() throws Exception {
        BranchRepository repository = mock(BranchRepository.class);
        BranchAnalysisProcessor processor = mock(BranchAnalysisProcessor.class);
        BranchRepository.StaleRetryCandidate candidate = candidate(2, true, "abc123");
        when(repository.findStaleRetryCandidates(BranchHealthStatus.STALE))
                .thenReturn(List.of(candidate));

        new BranchHealthScheduler(repository, processor).retryStaleBranches();

        verify(processor).process(
                any(BranchProcessRequest.class),
                notNull());
        assertThat(BranchHealthScheduler.class
                .getMethod("retryStaleBranches")
                .isAnnotationPresent(Transactional.class))
                .isFalse();
    }

    @Test
    void scheduledRetryCanEmitProgressWithoutAnAttachedObserver() throws Exception {
        BranchRepository repository = mock(BranchRepository.class);
        BranchAnalysisProcessor processor = mock(BranchAnalysisProcessor.class);
        BranchRepository.StaleRetryCandidate retryable = candidate(2, true, "abc123");
        when(repository.findStaleRetryCandidates(BranchHealthStatus.STALE))
                .thenReturn(List.of(retryable));
        doAnswer(invocation -> {
            @SuppressWarnings("unchecked")
            java.util.function.Consumer<Map<String, Object>> observer = invocation.getArgument(1);
            EventNotificationEmitter.emitStatus(observer, "started", "Started");
            return Map.of("status", "accepted");
        }).when(processor).process(any(BranchProcessRequest.class), notNull());

        new BranchHealthScheduler(repository, processor).retryStaleBranches();

        verify(processor).process(any(BranchProcessRequest.class), notNull());
    }

    @Test
    void retryCeilingDoesNotWarnEveryScheduledRun() throws Exception {
        BranchRepository repository = mock(BranchRepository.class);
        BranchAnalysisProcessor processor = mock(BranchAnalysisProcessor.class);
        BranchRepository.StaleRetryCandidate atCeiling = candidate(10, true, "abc123");
        when(repository.findStaleRetryCandidates(BranchHealthStatus.STALE))
                .thenReturn(List.of(atCeiling));
        Logger logger = (Logger) LoggerFactory.getLogger(BranchHealthScheduler.class);
        ListAppender<ILoggingEvent> appender = new ListAppender<>();
        appender.start();
        logger.addAppender(appender);

        try {
            BranchHealthScheduler scheduler = new BranchHealthScheduler(repository, processor);
            scheduler.retryStaleBranches();
            scheduler.retryStaleBranches();
        } finally {
            logger.detachAppender(appender);
        }

        verify(processor, never()).process(any(), any());
        assertThat(appender.list)
                .noneMatch(event -> event.getLevel() == Level.WARN
                        && event.getFormattedMessage().contains("max retries"));
    }

    @Test
    void missingCommitDoesNotWarnEveryScheduledRun() throws Exception {
        BranchRepository repository = mock(BranchRepository.class);
        BranchAnalysisProcessor processor = mock(BranchAnalysisProcessor.class);
        BranchRepository.StaleRetryCandidate missingCommit = candidate(2, true, " ");
        when(repository.findStaleRetryCandidates(BranchHealthStatus.STALE))
                .thenReturn(List.of(missingCommit));
        Logger logger = (Logger) LoggerFactory.getLogger(BranchHealthScheduler.class);
        ListAppender<ILoggingEvent> appender = new ListAppender<>();
        appender.start();
        logger.addAppender(appender);

        try {
            BranchHealthScheduler scheduler = new BranchHealthScheduler(repository, processor);
            scheduler.retryStaleBranches();
            scheduler.retryStaleBranches();
        } finally {
            logger.detachAppender(appender);
        }

        verify(processor, never()).process(any(), any());
        assertThat(appender.list)
                .noneMatch(event -> event.getLevel() == Level.WARN
                        && event.getFormattedMessage().contains("no commit hash"));
    }

    private static BranchRepository.StaleRetryCandidate candidate(
            int failures,
            boolean analysisEnabled,
            String commitHash) {
        BranchRepository.StaleRetryCandidate candidate =
                mock(BranchRepository.StaleRetryCandidate.class);
        when(candidate.getBranchId()).thenReturn(7L);
        when(candidate.getProjectId()).thenReturn(42L);
        when(candidate.getBranchName()).thenReturn("main");
        when(candidate.getCommitHash()).thenReturn(commitHash);
        when(candidate.getConsecutiveFailures()).thenReturn(failures);
        when(candidate.getBranchAnalysisEnabled()).thenReturn(analysisEnabled);
        return candidate;
    }
}
