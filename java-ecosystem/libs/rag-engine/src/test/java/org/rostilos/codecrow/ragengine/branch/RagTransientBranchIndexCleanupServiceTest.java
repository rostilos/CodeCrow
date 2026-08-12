package org.rostilos.codecrow.ragengine.branch;

import ch.qos.logback.classic.Level;
import ch.qos.logback.classic.Logger;
import ch.qos.logback.classic.spi.ILoggingEvent;
import ch.qos.logback.core.read.ListAppender;
import org.junit.jupiter.api.Test;
import org.rostilos.codecrow.core.model.project.config.ProjectConfig;
import org.rostilos.codecrow.core.model.project.config.RagConfig;
import org.rostilos.codecrow.core.model.rag.RagBranchIndexGenerationStatus;
import org.rostilos.codecrow.core.persistence.repository.rag.RagBranchIndexGenerationRepository;
import org.rostilos.codecrow.core.persistence.repository.rag.RagBranchIndexRepository;
import org.rostilos.codecrow.ragengine.client.RagPipelineClient;
import org.slf4j.LoggerFactory;
import org.springframework.transaction.annotation.Transactional;

import java.time.OffsetDateTime;
import java.util.List;

import static org.assertj.core.api.Assertions.assertThat;
import static org.mockito.Mockito.mock;
import static org.mockito.Mockito.inOrder;
import static org.mockito.Mockito.never;
import static org.mockito.Mockito.times;
import static org.mockito.Mockito.verify;
import static org.mockito.Mockito.verifyNoInteractions;
import static org.mockito.Mockito.when;
import static org.mockito.ArgumentMatchers.anyString;
import static org.mockito.ArgumentMatchers.any;
import static org.mockito.ArgumentMatchers.eq;

class RagTransientBranchIndexCleanupServiceTest {

    @Test
    void deletesExpiredGenerationOutsideSchedulerTransactionAndRechecksRegistry() throws Exception {
        RagBranchIndexRepository branches = mock(RagBranchIndexRepository.class);
        RagBranchIndexGenerationRepository generations =
                mock(RagBranchIndexGenerationRepository.class);
        RagPipelineClient pipeline = mock(RagPipelineClient.class);
        var index = expiredCandidate();
        var generation = generation(100L, "opaque-transient-target");
        when(branches.findTransientCleanupCandidates()).thenReturn(List.of(index));
        when(generations.findCleanupCandidatesByBranchIndexId(10L))
                .thenReturn(List.of(generation));
        when(branches.claimExpiredTransientForDeletion(
                eq(10L), any(), any(), anyString(), any())).thenReturn(1);
        when(branches.heartbeatTransientDeletionClaim(
                eq(10L), anyString(), any())).thenReturn(1);
        when(pipeline.deleteBranchWithOutcome(
                "workspace", "namespace", "release/candidate",
                "opaque-transient-target", "revision-100", "manifest-100"))
                .thenReturn(RagPipelineClient.BranchDeletionOutcome.success(
                        "opaque-transient-target"));
        when(branches.deleteClaimedTransientById(eq(10L), anyString()))
                .thenReturn(1);

        new RagTransientBranchIndexCleanupService(
                branches, generations, pipeline).cleanupExpired();

        verify(branches).deleteClaimedTransientById(eq(10L), anyString());
        verify(pipeline).deleteBranchWithOutcome(
                "workspace", "namespace", "release/candidate",
                "opaque-transient-target", "revision-100", "manifest-100");
        assertThat(RagTransientBranchIndexCleanupService.class
                .getMethod("cleanupExpired")
                .isAnnotationPresent(Transactional.class))
                .isFalse();
    }

    @Test
    void serviceFailureStopsRemainingGenerationsAndWarnsOnlyOnDegradedTransition() {
        RagBranchIndexRepository branches = mock(RagBranchIndexRepository.class);
        RagBranchIndexGenerationRepository generations =
                mock(RagBranchIndexGenerationRepository.class);
        RagPipelineClient pipeline = mock(RagPipelineClient.class);
        var index = expiredCandidate();
        var first = generation(100L, "target-a");
        var second = generation(101L, "target-b");
        when(branches.findTransientCleanupCandidates()).thenReturn(List.of(index));
        when(generations.findCleanupCandidatesByBranchIndexId(10L))
                .thenReturn(List.of(first, second));
        when(branches.claimExpiredTransientForDeletion(
                eq(10L), any(), any(), anyString(), any())).thenReturn(1);
        when(branches.heartbeatTransientDeletionClaim(
                eq(10L), anyString(), any())).thenReturn(1);
        when(pipeline.deleteBranchWithOutcome(
                "workspace", "namespace", "release/candidate", "target-a",
                "revision-100", "manifest-100"))
                .thenReturn(RagPipelineClient.BranchDeletionOutcome.failure(
                        "target-a",
                        RagPipelineClient.BranchDeletionFailure.SERVICE,
                        503,
                        "unavailable"));
        Logger logger = (Logger) LoggerFactory.getLogger(
                RagTransientBranchIndexCleanupService.class);
        ListAppender<ILoggingEvent> appender = new ListAppender<>();
        appender.start();
        logger.addAppender(appender);

        try {
            RagTransientBranchIndexCleanupService service =
                    new RagTransientBranchIndexCleanupService(branches, generations, pipeline);
            service.cleanupExpired();
            service.cleanupExpired();
        } finally {
            logger.detachAppender(appender);
        }

        verify(pipeline, times(2)).deleteBranchWithOutcome(
                "workspace", "namespace", "release/candidate", "target-a",
                "revision-100", "manifest-100");
        verify(pipeline, never()).deleteBranchWithOutcome(
                "workspace", "namespace", "release/candidate", "target-b",
                "revision-101", "manifest-101");
        verify(branches, never()).deleteClaimedTransientById(
                org.mockito.ArgumentMatchers.anyLong(), anyString());
        assertThat(appender.list.stream()
                .filter(event -> event.getLevel() == Level.WARN
                        && event.getFormattedMessage().contains("target=target-a")))
                .hasSize(1);
    }

    @Test
    void refreshedCandidateThatLosesAtomicClaimIsNeverDeleted() {
        RagBranchIndexRepository branches = mock(RagBranchIndexRepository.class);
        RagBranchIndexGenerationRepository generations =
                mock(RagBranchIndexGenerationRepository.class);
        RagPipelineClient pipeline = mock(RagPipelineClient.class);
        var index = expiredCandidate();
        when(branches.findTransientCleanupCandidates())
                .thenReturn(List.of(index));
        when(branches.claimExpiredTransientForDeletion(
                eq(10L), any(), any(), anyString(), any())).thenReturn(0);

        new RagTransientBranchIndexCleanupService(
                branches, generations, pipeline).cleanupExpired();

        verifyNoInteractions(generations, pipeline);
    }

    @Test
    void partialPhysicalCleanupKeepsDurableClaimAndDeletesActiveGenerationLast() {
        RagBranchIndexRepository branches = mock(RagBranchIndexRepository.class);
        RagBranchIndexGenerationRepository generations =
                mock(RagBranchIndexGenerationRepository.class);
        RagPipelineClient pipeline = mock(RagPipelineClient.class);
        var index = expiredCandidate();
        var superseded = generation(100L, "superseded", RagBranchIndexGenerationStatus.SUPERSEDED);
        var active = generation(101L, "active", RagBranchIndexGenerationStatus.ACTIVE);
        when(branches.findTransientCleanupCandidates())
                .thenReturn(List.of(index));
        when(branches.claimExpiredTransientForDeletion(
                eq(10L), any(), any(), anyString(), any())).thenReturn(1);
        when(branches.heartbeatTransientDeletionClaim(
                eq(10L), anyString(), any())).thenReturn(1);
        when(generations.findCleanupCandidatesByBranchIndexId(10L))
                .thenReturn(List.of(active, superseded));
        when(pipeline.deleteBranchWithOutcome(
                "workspace", "namespace", "release/candidate", "superseded",
                "revision-100", "manifest-100"))
                .thenReturn(RagPipelineClient.BranchDeletionOutcome.success("superseded"));
        when(pipeline.deleteBranchWithOutcome(
                "workspace", "namespace", "release/candidate", "active",
                "revision-101", "manifest-101"))
                .thenReturn(RagPipelineClient.BranchDeletionOutcome.failure(
                        "active", RagPipelineClient.BranchDeletionFailure.SERVICE,
                        503, "unavailable"));

        new RagTransientBranchIndexCleanupService(
                branches, generations, pipeline).cleanupExpired();

        var ordered = inOrder(pipeline);
        ordered.verify(pipeline).deleteBranchWithOutcome(
                "workspace", "namespace", "release/candidate", "superseded",
                "revision-100", "manifest-100");
        ordered.verify(pipeline).deleteBranchWithOutcome(
                "workspace", "namespace", "release/candidate", "active",
                "revision-101", "manifest-101");
        verify(branches, never()).cancelTransientDeletion(eq(10L), anyString());
        verify(branches, never()).deleteClaimedTransientById(eq(10L), anyString());
    }

    @Test
    void transportFailureKeepsClaimBecauseRemoteDeletionMayHaveCompleted() {
        RagBranchIndexRepository branches = mock(RagBranchIndexRepository.class);
        RagBranchIndexGenerationRepository generations =
                mock(RagBranchIndexGenerationRepository.class);
        RagPipelineClient pipeline = mock(RagPipelineClient.class);
        var index = expiredCandidate();
        var generation = generation(100L, "uncertain-target");
        when(branches.findTransientCleanupCandidates())
                .thenReturn(List.of(index));
        when(branches.claimExpiredTransientForDeletion(
                eq(10L), any(), any(), anyString(), any())).thenReturn(1);
        when(branches.heartbeatTransientDeletionClaim(
                eq(10L), anyString(), any())).thenReturn(1);
        when(generations.findCleanupCandidatesByBranchIndexId(10L))
                .thenReturn(List.of(generation));
        when(pipeline.deleteBranchWithOutcome(
                "workspace", "namespace", "release/candidate", "uncertain-target",
                "revision-100", "manifest-100"))
                .thenReturn(RagPipelineClient.BranchDeletionOutcome.failure(
                        "uncertain-target",
                        RagPipelineClient.BranchDeletionFailure.TRANSPORT,
                        null,
                        "connection reset"));

        new RagTransientBranchIndexCleanupService(
                branches, generations, pipeline).cleanupExpired();

        verify(branches, never()).cancelTransientDeletion(eq(10L), anyString());
        verify(branches, never()).deleteClaimedTransientById(eq(10L), anyString());
    }

    @Test
    void targetRejectionKeepsActiveTargetReadableAndReleasesUnusedClaim() {
        RagBranchIndexRepository branches = mock(RagBranchIndexRepository.class);
        RagBranchIndexGenerationRepository generations =
                mock(RagBranchIndexGenerationRepository.class);
        RagPipelineClient pipeline = mock(RagPipelineClient.class);
        var index = expiredCandidate();
        var rejected = generation(
                100L, "rejected", RagBranchIndexGenerationStatus.SUPERSEDED);
        var active = generation(
                101L, "active", RagBranchIndexGenerationStatus.ACTIVE);
        when(branches.findTransientCleanupCandidates()).thenReturn(List.of(index));
        when(branches.claimExpiredTransientForDeletion(
                eq(10L), any(), any(), anyString(), any())).thenReturn(1);
        when(branches.heartbeatTransientDeletionClaim(
                eq(10L), anyString(), any())).thenReturn(1);
        when(generations.findCleanupCandidatesByBranchIndexId(10L))
                .thenReturn(List.of(active, rejected));
        when(pipeline.deleteBranchWithOutcome(
                "workspace", "namespace", "release/candidate", "rejected",
                "revision-100", "manifest-100"))
                .thenReturn(RagPipelineClient.BranchDeletionOutcome.failure(
                        "rejected", RagPipelineClient.BranchDeletionFailure.TARGET,
                        422, "manifest receipt rejected"));

        new RagTransientBranchIndexCleanupService(
                branches, generations, pipeline).cleanupExpired();

        verify(pipeline, never()).deleteBranchWithOutcome(
                "workspace", "namespace", "release/candidate", "active",
                "revision-101", "manifest-101");
        verify(branches).cancelTransientDeletion(eq(10L), anyString());
        verify(branches, never()).deleteClaimedTransientById(eq(10L), anyString());
    }

    @Test
    void globallyDisabledRagSilentlyRetainsRegistryRow() {
        RagBranchIndexRepository branches = mock(RagBranchIndexRepository.class);
        RagBranchIndexGenerationRepository generations =
                mock(RagBranchIndexGenerationRepository.class);
        RagPipelineClient pipeline = mock(RagPipelineClient.class);
        var index = expiredCandidate();
        var generation = generation(100L, "disabled-target");
        when(branches.findTransientCleanupCandidates())
                .thenReturn(List.of(index));
        when(branches.claimExpiredTransientForDeletion(
                eq(10L), any(), any(), anyString(), any())).thenReturn(1);
        when(branches.heartbeatTransientDeletionClaim(
                eq(10L), anyString(), any())).thenReturn(1);
        when(generations.findCleanupCandidatesByBranchIndexId(10L))
                .thenReturn(List.of(generation));
        when(pipeline.deleteBranchWithOutcome(
                "workspace", "namespace", "release/candidate", "disabled-target",
                "revision-100", "manifest-100"))
                .thenReturn(RagPipelineClient.BranchDeletionOutcome.failure(
                        "disabled-target",
                        RagPipelineClient.BranchDeletionFailure.TARGET,
                        null,
                        "RAG disabled"));
        Logger logger = (Logger) LoggerFactory.getLogger(
                RagTransientBranchIndexCleanupService.class);
        ListAppender<ILoggingEvent> appender = new ListAppender<>();
        appender.start();
        logger.addAppender(appender);

        try {
            new RagTransientBranchIndexCleanupService(
                    branches, generations, pipeline).cleanupExpired();
        } finally {
            logger.detachAppender(appender);
        }

        verify(branches).cancelTransientDeletion(eq(10L), anyString());
        verify(branches, never()).deleteClaimedTransientById(eq(10L), anyString());
        assertThat(appender.list).noneMatch(event -> event.getLevel() == Level.WARN);
    }

    private static RagBranchIndexRepository.TransientCleanupCandidate expiredCandidate() {
        var candidate = mock(RagBranchIndexRepository.TransientCleanupCandidate.class);
        when(candidate.getBranchIndexId()).thenReturn(10L);
        when(candidate.getProjectId()).thenReturn(42L);
        when(candidate.getWorkspaceName()).thenReturn("workspace");
        when(candidate.getProjectNamespace()).thenReturn("namespace");
        when(candidate.getBranchName()).thenReturn("release/candidate");
        when(candidate.getLastAccessedAt()).thenReturn(OffsetDateTime.now().minusDays(31));
        when(candidate.getProjectConfiguration()).thenReturn(new ProjectConfig(
                false, "master", null,
                new RagConfig(true, "master", null, null,
                        true, 30, List.of("develop"), true)));
        return candidate;
    }

    private static RagBranchIndexGenerationRepository.CleanupGenerationCandidate generation(
            Long id,
            String target) {
        return generation(id, target, RagBranchIndexGenerationStatus.SUPERSEDED);
    }

    private static RagBranchIndexGenerationRepository.CleanupGenerationCandidate generation(
            Long id,
            String target,
            RagBranchIndexGenerationStatus status) {
        var candidate = mock(
                RagBranchIndexGenerationRepository.CleanupGenerationCandidate.class);
        when(candidate.getGenerationId()).thenReturn(id);
        when(candidate.getCollectionName()).thenReturn(target);
        when(candidate.getRevision()).thenReturn("revision-" + id);
        when(candidate.getManifestDigest()).thenReturn("manifest-" + id);
        when(candidate.getStatus()).thenReturn(status);
        return candidate;
    }
}
