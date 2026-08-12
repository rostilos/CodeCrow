package org.rostilos.codecrow.ragengine.branch;

import ch.qos.logback.classic.Level;
import ch.qos.logback.classic.Logger;
import ch.qos.logback.classic.spi.ILoggingEvent;
import ch.qos.logback.core.read.ListAppender;
import org.junit.jupiter.api.Test;
import org.rostilos.codecrow.core.model.rag.RagBranchIndexKind;
import org.rostilos.codecrow.core.persistence.repository.rag.RagBranchIndexRepository;
import org.rostilos.codecrow.ragengine.client.RagPipelineClient;
import org.slf4j.LoggerFactory;

import java.io.IOException;
import java.util.List;
import java.util.Optional;

import static org.assertj.core.api.Assertions.assertThat;
import static org.mockito.Mockito.*;

class RagBranchOperatorAliasReconciliationServiceTest {

    @Test
    void restoresReadableAliasesForDurableAndPrimaryGenerationsOnly() throws Exception {
        RagBranchIndexRepository repository = mock(RagBranchIndexRepository.class);
        RagPipelineClient client = mock(RagPipelineClient.class);
        RagBranchOperatorAliasReconciliationService service =
                new RagBranchOperatorAliasReconciliationService(repository, client);

        var primary = candidate("main", RagBranchIndexKind.PRIMARY, "main-target");
        var durable = candidate("develop", RagBranchIndexKind.DURABLE, "develop-target");
        when(repository.findOperatorAliasCandidates()).thenReturn(List.of(primary, durable));
        stubCurrent(repository, primary, durable);

        service.reconcileActiveGenerationAliases();

        verify(client).publishGenerationAliases(
                "workspace", "project", "main", "revision", "main-target", "manifest-main", true, true);
        verify(client).publishGenerationAliases(
                "workspace", "project", "develop", "revision", "develop-target", "manifest-develop", true, false);
        verifyNoMoreInteractions(client);
    }

    @Test
    void transportFailureStopsRemainingCandidatesUntilNextScheduledRun() throws Exception {
        RagBranchIndexRepository repository = mock(RagBranchIndexRepository.class);
        RagPipelineClient client = mock(RagPipelineClient.class);
        RagBranchOperatorAliasReconciliationService service =
                new RagBranchOperatorAliasReconciliationService(repository, client);
        var first = candidate("main", RagBranchIndexKind.PRIMARY, "main-target");
        var second = candidate("develop", RagBranchIndexKind.DURABLE, "develop-target");
        when(repository.findOperatorAliasCandidates()).thenReturn(List.of(first, second));
        stubCurrent(repository, first, second);
        doThrow(new IOException("timeout")).when(client).publishGenerationAliases(
                eq("workspace"), eq("project"), eq("main"), anyString(), anyString(), anyString(),
                anyBoolean(), anyBoolean());

        service.reconcileActiveGenerationAliases();

        verify(client).publishGenerationAliases(
                "workspace", "project", "main", "revision", "main-target", "manifest-main", true, true);
        verify(client, never()).publishGenerationAliases(
                eq("workspace"), eq("project"), eq("develop"), anyString(), anyString(), anyString(),
                anyBoolean(), anyBoolean());
    }

    @Test
    void rateLimitStopsRemainingCandidatesUntilNextScheduledRun() throws Exception {
        RagBranchIndexRepository repository = mock(RagBranchIndexRepository.class);
        RagPipelineClient client = mock(RagPipelineClient.class);
        RagBranchOperatorAliasReconciliationService service =
                new RagBranchOperatorAliasReconciliationService(repository, client);
        var first = candidate("main", RagBranchIndexKind.PRIMARY, "main-target");
        var second = candidate("develop", RagBranchIndexKind.DURABLE, "develop-target");
        when(repository.findOperatorAliasCandidates()).thenReturn(List.of(first, second));
        stubCurrent(repository, first, second);
        doThrow(new RagPipelineClient.RagApiException(429, "rate limited"))
                .when(client).publishGenerationAliases(
                        eq("workspace"), eq("project"), eq("main"), anyString(), anyString(), anyString(),
                        anyBoolean(), anyBoolean());

        service.reconcileActiveGenerationAliases();

        verify(client, never()).publishGenerationAliases(
                eq("workspace"), eq("project"), eq("develop"), anyString(), anyString(), anyString(),
                anyBoolean(), anyBoolean());
    }

    @Test
    void generationValidationRejectionDoesNotBlockOtherCandidates() throws Exception {
        RagBranchIndexRepository repository = mock(RagBranchIndexRepository.class);
        RagPipelineClient client = mock(RagPipelineClient.class);
        RagBranchOperatorAliasReconciliationService service =
                new RagBranchOperatorAliasReconciliationService(repository, client);
        var first = candidate("main", RagBranchIndexKind.PRIMARY, "main-target");
        var second = candidate("develop", RagBranchIndexKind.DURABLE, "develop-target");
        when(repository.findOperatorAliasCandidates()).thenReturn(List.of(first, second));
        stubCurrent(repository, first, second);
        doThrow(new RagPipelineClient.RagApiException(409, "manifest mismatch"))
                .when(client).publishGenerationAliases(
                        eq("workspace"), eq("project"), eq("main"), anyString(), anyString(), anyString(),
                        anyBoolean(), anyBoolean());

        service.reconcileActiveGenerationAliases();

        verify(client).publishGenerationAliases(
                "workspace", "project", "develop", "revision", "develop-target", "manifest-develop", true, false);
    }

    @Test
    void repairsAliasWithNewGenerationWhenRegistryChangesDuringPublication() throws Exception {
        RagBranchIndexRepository repository = mock(RagBranchIndexRepository.class);
        RagPipelineClient client = mock(RagPipelineClient.class);
        RagBranchOperatorAliasReconciliationService service =
                new RagBranchOperatorAliasReconciliationService(repository, client);
        var generationA = candidate(
                "main", RagBranchIndexKind.PRIMARY, "generation-a", 10L, 100L);
        var generationB = candidate(
                "main", RagBranchIndexKind.PRIMARY, "generation-b", 10L, 101L);
        when(repository.findOperatorAliasCandidates()).thenReturn(List.of(generationA));
        when(repository.findOperatorAliasCandidateById(10L)).thenReturn(
                Optional.of(generationA), Optional.of(generationB), Optional.of(generationB));

        service.reconcileActiveGenerationAliases();

        var inOrder = inOrder(client);
        inOrder.verify(client).publishGenerationAliases(
                "workspace", "project", "main", "revision", "generation-a", "manifest-main", true, true);
        inOrder.verify(client).publishGenerationAliases(
                "workspace", "project", "main", "revision", "generation-b", "manifest-main", true, true);
    }

    @Test
    void persistentOutageWarnsOnceAndLogsRecoveryOnce() throws Exception {
        RagBranchIndexRepository repository = mock(RagBranchIndexRepository.class);
        RagPipelineClient client = mock(RagPipelineClient.class);
        RagBranchOperatorAliasReconciliationService service =
                new RagBranchOperatorAliasReconciliationService(repository, client);
        var candidate = candidate("main", RagBranchIndexKind.PRIMARY, "main-target");
        when(repository.findOperatorAliasCandidates()).thenReturn(List.of(candidate));
        stubCurrent(repository, candidate);
        doThrow(new IOException("timeout"))
                .doThrow(new IOException("timeout"))
                .doNothing()
                .when(client).publishGenerationAliases(
                        anyString(), anyString(), anyString(), anyString(), anyString(), anyString(),
                        anyBoolean(), anyBoolean());
        Logger logger = (Logger) LoggerFactory.getLogger(
                RagBranchOperatorAliasReconciliationService.class);
        ListAppender<ILoggingEvent> appender = new ListAppender<>();
        appender.start();
        logger.addAppender(appender);
        try {
            service.reconcileActiveGenerationAliases();
            service.reconcileActiveGenerationAliases();
            service.reconcileActiveGenerationAliases();
        } finally {
            logger.detachAppender(appender);
            appender.stop();
        }

        assertThat(appender.list.stream()
                .filter(event -> event.getLevel() == Level.WARN)
                .map(ILoggingEvent::getFormattedMessage))
                .containsExactly("RAG alias reconciliation stopped after a transport failure; "
                        + "remaining candidates will retry next run: timeout");
        assertThat(appender.list.stream()
                .filter(event -> event.getLevel() == Level.INFO)
                .map(ILoggingEvent::getFormattedMessage))
                .anyMatch(message -> message.contains("retry next run: timeout"))
                .contains("RAG alias reconciliation recovered");
    }

    @Test
    void persistentCandidateRejectionWarnsOnlyOnDegradedTransition() throws Exception {
        RagBranchIndexRepository repository = mock(RagBranchIndexRepository.class);
        RagPipelineClient client = mock(RagPipelineClient.class);
        RagBranchOperatorAliasReconciliationService service =
                new RagBranchOperatorAliasReconciliationService(repository, client);
        var candidate = candidate("main", RagBranchIndexKind.PRIMARY, "main-target");
        when(repository.findOperatorAliasCandidates()).thenReturn(List.of(candidate));
        stubCurrent(repository, candidate);
        doThrow(new RagPipelineClient.RagApiException(409, "manifest mismatch"))
                .doThrow(new RagPipelineClient.RagApiException(409, "manifest mismatch"))
                .doNothing()
                .when(client).publishGenerationAliases(
                        anyString(), anyString(), anyString(), anyString(), anyString(), anyString(),
                        anyBoolean(), anyBoolean());
        Logger logger = (Logger) LoggerFactory.getLogger(
                RagBranchOperatorAliasReconciliationService.class);
        ListAppender<ILoggingEvent> appender = new ListAppender<>();
        appender.start();
        logger.addAppender(appender);
        try {
            service.reconcileActiveGenerationAliases();
            service.reconcileActiveGenerationAliases();
            service.reconcileActiveGenerationAliases();
        } finally {
            logger.detachAppender(appender);
            appender.stop();
        }

        assertThat(appender.list.stream()
                .filter(event -> event.getLevel() == Level.WARN)
                .map(ILoggingEvent::getFormattedMessage))
                .containsExactly("RAG alias reconciliation completed with 1 rejected candidate(s); "
                        + "they will retry next run");
        assertThat(appender.list.stream()
                .filter(event -> event.getLevel() == Level.INFO)
                .map(ILoggingEvent::getFormattedMessage))
                .contains("RAG alias reconciliation recovered");
    }

    private static void stubCurrent(
            RagBranchIndexRepository repository,
            RagBranchIndexRepository.OperatorAliasCandidate... candidates) {
        for (var candidate : candidates) {
            when(repository.findOperatorAliasCandidateById(candidate.getBranchIndexId()))
                    .thenReturn(Optional.of(candidate));
        }
    }

    private static RagBranchIndexRepository.OperatorAliasCandidate candidate(
            String branch,
            RagBranchIndexKind kind,
            String target) {
        var candidate = mock(RagBranchIndexRepository.OperatorAliasCandidate.class);
        when(candidate.getBranchIndexId()).thenReturn(
                "main".equals(branch) ? 10L : 20L);
        when(candidate.getGenerationId()).thenReturn(
                "main".equals(branch) ? 100L : 200L);
        when(candidate.getProjectId()).thenReturn(1L);
        when(candidate.getWorkspaceName()).thenReturn("workspace");
        when(candidate.getProjectNamespace()).thenReturn("project");
        when(candidate.getBranchName()).thenReturn(branch);
        when(candidate.getRevision()).thenReturn("revision");
        when(candidate.getCollectionName()).thenReturn(target);
        when(candidate.getManifestDigest()).thenReturn("manifest-" + branch);
        when(candidate.getIndexKind()).thenReturn(kind);
        return candidate;
    }

    private static RagBranchIndexRepository.OperatorAliasCandidate candidate(
            String branch,
            RagBranchIndexKind kind,
            String target,
            Long branchIndexId,
            Long generationId) {
        var candidate = candidate(branch, kind, target);
        when(candidate.getBranchIndexId()).thenReturn(branchIndexId);
        when(candidate.getGenerationId()).thenReturn(generationId);
        return candidate;
    }
}
