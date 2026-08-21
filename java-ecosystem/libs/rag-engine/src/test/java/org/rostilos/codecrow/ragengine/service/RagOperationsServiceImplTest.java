package org.rostilos.codecrow.ragengine.service;

import ch.qos.logback.classic.Level;
import ch.qos.logback.classic.Logger;
import ch.qos.logback.classic.spi.ILoggingEvent;
import ch.qos.logback.core.read.ListAppender;
import org.junit.jupiter.api.BeforeEach;
import org.junit.jupiter.api.Test;
import org.junit.jupiter.api.extension.ExtendWith;
import org.hibernate.LazyInitializationException;
import org.mockito.Mock;
import org.mockito.junit.jupiter.MockitoExtension;
import org.rostilos.codecrow.analysisengine.service.AnalysisLockService;
import org.rostilos.codecrow.core.model.analysis.RagIndexStatus;
import org.rostilos.codecrow.core.model.branch.Branch;
import org.rostilos.codecrow.core.model.job.Job;
import org.rostilos.codecrow.core.model.job.JobTriggerSource;
import org.rostilos.codecrow.core.model.project.Project;
import org.rostilos.codecrow.core.model.project.config.BranchAnalysisConfig;
import org.rostilos.codecrow.core.model.project.config.ProjectConfig;
import org.rostilos.codecrow.core.model.project.config.RagConfig;
import org.rostilos.codecrow.core.model.rag.RagBranchIndex;
import org.rostilos.codecrow.core.model.rag.RagBranchIndexGeneration;
import org.rostilos.codecrow.core.model.rag.RagBranchIndexKind;
import org.rostilos.codecrow.core.model.vcs.VcsConnection;
import org.rostilos.codecrow.core.model.vcs.VcsRepoBinding;
import org.rostilos.codecrow.core.model.workspace.Workspace;
import org.rostilos.codecrow.core.persistence.repository.rag.RagBranchIndexRepository;
import org.rostilos.codecrow.core.persistence.repository.rag.RagBranchIndexGenerationRepository;
import org.rostilos.codecrow.core.service.AnalysisJobService;
import org.rostilos.codecrow.ragengine.client.RagPipelineClient;
import org.rostilos.codecrow.ragengine.branch.BranchIndexGenerationBuildService;
import org.rostilos.codecrow.ragengine.branch.BranchIndexBuildAdmissionService;
import org.rostilos.codecrow.ragengine.branch.LegacyRagJobLeaseService;
import org.rostilos.codecrow.ragengine.branch.LegacyRagUpdateCompletionService;
import org.rostilos.codecrow.vcsclient.VcsClient;
import org.rostilos.codecrow.vcsclient.VcsClientProvider;
import org.slf4j.LoggerFactory;
import org.springframework.test.util.ReflectionTestUtils;

import java.time.OffsetDateTime;
import java.util.List;
import java.util.Map;
import java.util.Optional;
import java.util.Set;
import java.util.concurrent.atomic.AtomicBoolean;
import java.util.function.Consumer;

import static org.assertj.core.api.Assertions.assertThat;
import static org.assertj.core.api.Assertions.assertThatThrownBy;
import static org.mockito.ArgumentMatchers.*;
import static org.mockito.Mockito.*;

@ExtendWith(MockitoExtension.class)
class RagOperationsServiceImplTest {

    @Mock
    private RagIndexTrackingService ragIndexTrackingService;

    @Mock
    private IncrementalRagUpdateService incrementalRagUpdateService;

    @Mock
    private AnalysisLockService analysisLockService;

    @Mock
    private AnalysisJobService analysisJobService;

    @Mock
    private RagBranchIndexRepository ragBranchIndexRepository;

    @Mock
    private RagBranchIndexGenerationRepository branchGenerationRepository;

    @Mock
    private VcsClientProvider vcsClientProvider;

    @Mock
    private RagPipelineClient ragPipelineClient;

    @Mock
    private BranchIndexBuildAdmissionService branchIndexBuildAdmissionService;

    @Mock
    private LegacyRagJobLeaseService legacyRagJobLeaseService;

    @Mock
    private LegacyRagJobLeaseService.JobLease legacyJobLease;

    @Mock
    private AnalysisLockService.LockLease legacyLockLease;

    @Mock
    private LegacyRagUpdateCompletionService legacyRagUpdateCompletionService;

    private RagOperationsServiceImpl service;
    private Project testProject;

    @BeforeEach
    void setUp() {
        service = new RagOperationsServiceImpl(
                ragIndexTrackingService,
                incrementalRagUpdateService,
                analysisLockService,
                analysisJobService,
                ragBranchIndexRepository,
                vcsClientProvider,
                ragPipelineClient,
                null,
                null,
                null,
                legacyRagJobLeaseService,
                legacyRagUpdateCompletionService);
        lenient().when(legacyRagJobLeaseService.start(anyLong()))
                .thenReturn(legacyJobLease);
        lenient().when(legacyJobLease.confirmOwnership()).thenReturn(true);
        lenient().when(analysisLockService.maintainLockLease(anyString(), anyInt()))
                .thenReturn(legacyLockLease);
        lenient().when(legacyLockLease.confirmOwnership()).thenReturn(true);
        lenient().when(legacyRagUpdateCompletionService.complete(
                any(), anyString(), anyString(), anyLong(), any(), anyBoolean(),
                anyInt(), anyInt(), any(), anySet())).thenReturn(true);
        ReflectionTestUtils.setField(
                service, "branchGenerationRepository", branchGenerationRepository);

        testProject = new Project();
        ReflectionTestUtils.setField(testProject, "id", 100L);
    }

    @Test
    void testIsRagEnabled_ApiDisabled() {
        ReflectionTestUtils.setField(service, "ragApiEnabled", false);

        boolean result = service.isRagEnabled(testProject);

        assertThat(result).isFalse();
    }

    @Test
    void exactFirstUseBuildsTargetSnapshotWithoutPrimaryToDevelopDiff() throws Exception {
        RagBranchIndexRegistryService registry = mock(RagBranchIndexRegistryService.class);
        BranchIndexGenerationBuildService builder = mock(BranchIndexGenerationBuildService.class);
        service = new RagOperationsServiceImpl(
                ragIndexTrackingService, incrementalRagUpdateService,
                analysisLockService, analysisJobService,
                ragBranchIndexRepository, vcsClientProvider,
                ragPipelineClient, registry, builder, branchIndexBuildAdmissionService);
        setupRagEnabled();
        setupVcsBinding();
        ReflectionTestUtils.setField(service, "ragApiEnabled", true);
        when(ragIndexTrackingService.isProjectIndexed(testProject)).thenReturn(true);
        when(incrementalRagUpdateService.shouldPerformIncrementalUpdate(testProject))
                .thenReturn(true);
        Job job = mock(Job.class);
        var prepared = new BranchIndexGenerationBuildService.PreparedBuild(
                30L, "physical-target", false, null, "exact-feature-lock");
        when(branchIndexBuildAdmissionService.admit(
                testProject, "feature", "develop-400", RagBranchIndexKind.DURABLE,
                JobTriggerSource.WEBHOOK, "exact-feature-lock",
                BranchIndexBuildAdmissionService.BuildOrigin.AUTOMATIC))
                .thenReturn(new BranchIndexBuildAdmissionService.AdmittedBuild(
                        job, prepared,
                        BranchIndexBuildAdmissionService.ProjectStatusAdmission.NONE));
        when(analysisLockService.acquireLock(
                eq(testProject), eq("feature"), any(), eq("develop-400"), isNull()))
                .thenReturn(Optional.of("exact-feature-lock"));
        VcsClient vcs = mock(VcsClient.class);
        when(vcsClientProvider.getClient(any())).thenReturn(vcs);
        when(vcs.getLatestCommitHash("my-workspace", "my-repo", "feature"))
                .thenReturn("develop-400");
        when(builder.execute(
                eq(testProject), any(), eq("my-workspace"), eq("my-repo"),
                eq("feature"), eq("develop-400"),
                eq(org.rostilos.codecrow.core.model.rag.RagBranchIndexKind.DURABLE),
                nullable(List.class), nullable(List.class), eq(prepared), isNull()))
                .thenReturn(Map.of(
                        "generation_manifest_sha256", "manifest-400",
                        "document_count", 231,
                        "chunk_count", 400,
                        "updatedFiles", 231,
                        "deletedFiles", 0,
                        "skippedFiles", 0));

        boolean ready = service.ensureBranchIndexForPrTarget(
                testProject, "feature", ignored -> { });

        assertThat(ready).isTrue();
        verify(vcs, never()).getBranchDiff(anyString(), anyString(), anyString(), anyString());
        verify(builder).execute(
                eq(testProject), any(), eq("my-workspace"), eq("my-repo"),
                eq("feature"), eq("develop-400"),
                eq(org.rostilos.codecrow.core.model.rag.RagBranchIndexKind.DURABLE),
                nullable(List.class), nullable(List.class), eq(prepared), isNull());
        verify(branchIndexBuildAdmissionService).admit(
                testProject, "feature", "develop-400", RagBranchIndexKind.DURABLE,
                JobTriggerSource.WEBHOOK, "exact-feature-lock",
                BranchIndexBuildAdmissionService.BuildOrigin.AUTOMATIC);
        verifyNoInteractions(registry);
    }

    @Test
    void exactMismatchUsesScalarProjectionWithoutTouchingDetachedActiveGenerationProxy()
            throws Exception {
        RagBranchIndexRegistryService registry = mock(RagBranchIndexRegistryService.class);
        BranchIndexGenerationBuildService builder = mock(BranchIndexGenerationBuildService.class);
        service = new RagOperationsServiceImpl(
                ragIndexTrackingService, incrementalRagUpdateService,
                analysisLockService, analysisJobService,
                ragBranchIndexRepository, vcsClientProvider,
                ragPipelineClient, registry, builder, branchIndexBuildAdmissionService);
        setupRagEnabled();
        setupVcsBinding();
        setupProjectWithWorkspaceAndNamespace();

        when(ragBranchIndexRepository.existsByProjectIdAndBranchName(100L, "main"))
                .thenReturn(true);
        RagBranchIndex detachedIndex = mock(RagBranchIndex.class);
        RagBranchIndexGeneration detachedGeneration = mock(
                RagBranchIndexGeneration.class);
        lenient().when(ragBranchIndexRepository.findByProjectIdAndBranchName(100L, "main"))
                .thenReturn(Optional.of(detachedIndex));
        lenient().when(detachedIndex.getActiveGeneration()).thenReturn(detachedGeneration);
        lenient().when(detachedGeneration.getRevision()).thenThrow(
                new LazyInitializationException("no Session"));
        when(ragBranchIndexRepository.markAccessedIfUnclaimed(
                eq(100L), eq("main"), any()))
                .thenReturn(1);
        RagBranchIndexRepository.ActiveGenerationCoordinates source =
                mock(RagBranchIndexRepository.ActiveGenerationCoordinates.class);
        // Simulate returning A after B became active. An older successful A
        // operation may exist, so automatic reconciliation must force a fresh
        // generation instead of reusing the superseded one.
        when(source.getRevision()).thenReturn("revision-b");
        AtomicBoolean lockAcquired = new AtomicBoolean();
        when(ragBranchIndexRepository.findActiveGenerationCoordinates(100L, "main"))
                .thenAnswer(ignored -> {
                    assertThat(lockAcquired.get()).isTrue();
                    return Optional.of(source);
                });
        when(incrementalRagUpdateService.shouldPerformIncrementalUpdate(testProject))
                .thenReturn(true);
        Job job = mock(Job.class);
        when(job.getId()).thenReturn(77L);
        var prepared = new BranchIndexGenerationBuildService.PreparedBuild(
                31L, "target-generation", false, null, "rag-lock");
        when(branchIndexBuildAdmissionService.admit(
                testProject, "main", "revision-a", RagBranchIndexKind.PRIMARY,
                JobTriggerSource.WEBHOOK, "rag-lock",
                BranchIndexBuildAdmissionService.BuildOrigin.AUTOMATIC))
                .thenReturn(new BranchIndexBuildAdmissionService.AdmittedBuild(
                        job, prepared,
                        BranchIndexBuildAdmissionService.ProjectStatusAdmission.UPDATING));
        when(analysisLockService.acquireLock(
                eq(testProject), eq("main"), any(), eq("revision-a"), isNull()))
                .thenAnswer(ignored -> {
                    lockAcquired.set(true);
                    return Optional.of("rag-lock");
                });

        when(builder.execute(
                eq(testProject), any(VcsConnection.class), eq("my-workspace"), eq("my-repo"),
                eq("main"), eq("revision-a"), eq(RagBranchIndexKind.PRIMARY),
                nullable(List.class), nullable(List.class), eq(prepared), isNull()))
                .thenReturn(Map.of(
                        "generation_manifest_sha256", "target-manifest",
                        "document_count", 214,
                        "chunk_count", 642,
                        "deletedFiles", 0));

        boolean result = service.triggerIncrementalUpdate(
                testProject, "main", "revision-a", "caller diff", ignored -> { });

        assertThat(result).isTrue();
        var sourceBindingOrder = inOrder(
                analysisLockService, ragBranchIndexRepository, builder);
        sourceBindingOrder.verify(analysisLockService).acquireLock(
                eq(testProject), eq("main"), any(), eq("revision-a"), isNull());
        sourceBindingOrder.verify(ragBranchIndexRepository)
                .findActiveGenerationCoordinates(100L, "main");
        sourceBindingOrder.verify(builder).execute(
                eq(testProject), any(VcsConnection.class), eq("my-workspace"), eq("my-repo"),
                eq("main"), eq("revision-a"), eq(RagBranchIndexKind.PRIMARY),
                nullable(List.class), nullable(List.class), eq(prepared), isNull());
        verifyNoInteractions(vcsClientProvider, registry);
        verify(incrementalRagUpdateService, never()).parseDiffForRag("caller diff");
        verify(analysisJobService).info(eq(job), eq("rag_init"),
                contains("Starting exact RAG generation rebuild"));
        verify(analysisJobService).info(eq(job), eq("rag_complete"),
                contains("214 documents, 642 chunks"));
        verify(branchIndexBuildAdmissionService).admit(
                testProject, "main", "revision-a", RagBranchIndexKind.PRIMARY,
                JobTriggerSource.WEBHOOK, "rag-lock",
                BranchIndexBuildAdmissionService.BuildOrigin.AUTOMATIC);
        verify(ragBranchIndexRepository, never())
                .findByProjectIdAndBranchName(anyLong(), anyString());
        verify(detachedIndex, never()).getActiveGeneration();
        verify(detachedGeneration, never()).getRevision();
        verify(ragBranchIndexRepository, never()).save(any());
    }

    @Test
    void exactPublicationProjectionFailureLeavesSucceededOperationOwnedForRecovery()
            throws Exception {
        BranchIndexGenerationBuildService builder = mock(
                BranchIndexGenerationBuildService.class);
        service = new RagOperationsServiceImpl(
                ragIndexTrackingService, incrementalRagUpdateService,
                analysisLockService, analysisJobService,
                ragBranchIndexRepository, vcsClientProvider,
                ragPipelineClient, mock(RagBranchIndexRegistryService.class),
                builder, branchIndexBuildAdmissionService);
        setupRagEnabled();
        setupVcsBinding();
        when(incrementalRagUpdateService.shouldPerformIncrementalUpdate(testProject))
                .thenReturn(true);
        when(analysisLockService.acquireLock(
                eq(testProject), eq("main"), any(), eq("revision-published"), isNull()))
                .thenReturn(Optional.of("rag-lock"));
        Job job = mock(Job.class);
        when(job.getId()).thenReturn(91L);
        var prepared = new BranchIndexGenerationBuildService.PreparedBuild(
                81L, "physical-generation", false, null, "rag-lock");
        when(branchIndexBuildAdmissionService.admit(
                testProject, "main", "revision-published", RagBranchIndexKind.PRIMARY,
                JobTriggerSource.WEBHOOK, "rag-lock",
                BranchIndexBuildAdmissionService.BuildOrigin.AUTOMATIC))
                .thenReturn(new BranchIndexBuildAdmissionService.AdmittedBuild(
                        job, prepared,
                        BranchIndexBuildAdmissionService.ProjectStatusAdmission.INDEXING));
        when(builder.execute(
                eq(testProject), any(VcsConnection.class), eq("my-workspace"), eq("my-repo"),
                eq("main"), eq("revision-published"), eq(RagBranchIndexKind.PRIMARY),
                nullable(List.class), nullable(List.class), eq(prepared), isNull()))
                .thenReturn(Map.of("document_count", 12, "chunk_count", 34));
        when(ragIndexTrackingService.reconcilePublishedGeneration(
                testProject, "main", "revision-published", 12, 34, 91L))
                .thenThrow(new IllegalStateException("status database unavailable"));

        boolean result = service.triggerIncrementalUpdate(
                testProject, "main", "revision-published", "ignored", ignored -> { });

        assertThat(result).isFalse();
        verify(builder).execute(
                eq(testProject), any(VcsConnection.class), eq("my-workspace"), eq("my-repo"),
                eq("main"), eq("revision-published"), eq(RagBranchIndexKind.PRIMARY),
                nullable(List.class), nullable(List.class), eq(prepared), isNull());
        verify(branchIndexBuildAdmissionService, never()).abortOperation(any(), anyString());
        verify(analysisJobService, never()).failJob(any(), anyString());
        verify(ragIndexTrackingService, never()).markIndexingFailed(any(), anyString(), any());
        verify(ragIndexTrackingService, never()).markIncrementalUpdateFailed(
                any(), anyString(), any());
        verify(analysisLockService).releaseLock("rag-lock");
    }

    @Test
    void exactLockCleanupFailureCannotReverseSuccessfulPublicationAndJob()
            throws Exception {
        BranchIndexGenerationBuildService builder = mock(
                BranchIndexGenerationBuildService.class);
        service = new RagOperationsServiceImpl(
                ragIndexTrackingService, incrementalRagUpdateService,
                analysisLockService, analysisJobService,
                ragBranchIndexRepository, vcsClientProvider,
                ragPipelineClient, mock(RagBranchIndexRegistryService.class),
                builder, branchIndexBuildAdmissionService);
        setupRagEnabled();
        setupVcsBinding();
        when(incrementalRagUpdateService.shouldPerformIncrementalUpdate(testProject))
                .thenReturn(true);
        when(analysisLockService.acquireLock(
                eq(testProject), eq("main"), any(), eq("revision-complete"), isNull()))
                .thenReturn(Optional.of("rag-lock"));
        Job job = mock(Job.class);
        when(job.getId()).thenReturn(91L);
        var prepared = new BranchIndexGenerationBuildService.PreparedBuild(
                81L, "physical-generation", false, null, "rag-lock");
        when(branchIndexBuildAdmissionService.admit(
                testProject, "main", "revision-complete", RagBranchIndexKind.PRIMARY,
                JobTriggerSource.WEBHOOK, "rag-lock",
                BranchIndexBuildAdmissionService.BuildOrigin.AUTOMATIC))
                .thenReturn(new BranchIndexBuildAdmissionService.AdmittedBuild(
                        job, prepared,
                        BranchIndexBuildAdmissionService.ProjectStatusAdmission.INDEXING));
        when(builder.execute(
                eq(testProject), any(VcsConnection.class), eq("my-workspace"), eq("my-repo"),
                eq("main"), eq("revision-complete"), eq(RagBranchIndexKind.PRIMARY),
                nullable(List.class), nullable(List.class), eq(prepared), isNull()))
                .thenReturn(Map.of("document_count", 12, "chunk_count", 34));
        doThrow(new IllegalStateException("lock database unavailable"))
                .when(analysisLockService).releaseLock("rag-lock");

        boolean result = service.triggerIncrementalUpdate(
                testProject, "main", "revision-complete", "ignored", ignored -> { });

        assertThat(result).isTrue();
        verify(ragIndexTrackingService).reconcilePublishedGeneration(
                testProject, "main", "revision-complete", 12, 34, 91L);
        verify(analysisJobService).completeJob(job, null);
        verify(analysisJobService, never()).failJob(any(), anyString());
        verify(analysisLockService).releaseLock("rag-lock");
    }

    @Test
    void exactIncrementalLockContentionSkipsBeforeBindingSource() {
        service = new RagOperationsServiceImpl(
                ragIndexTrackingService, incrementalRagUpdateService,
                analysisLockService, analysisJobService,
                ragBranchIndexRepository, vcsClientProvider,
                ragPipelineClient, mock(RagBranchIndexRegistryService.class),
                mock(BranchIndexGenerationBuildService.class));
        setupRagEnabled();
        when(incrementalRagUpdateService.shouldPerformIncrementalUpdate(testProject))
                .thenReturn(true);
        when(analysisLockService.acquireLock(
                eq(testProject), eq("main"), any(), eq("target-revision"), isNull()))
                .thenReturn(Optional.empty());
        @SuppressWarnings("unchecked")
        Consumer<Map<String, Object>> eventConsumer = mock(Consumer.class);

        boolean result = service.triggerIncrementalUpdate(
                testProject, "main", "target-revision", "caller diff", eventConsumer);

        assertThat(result).isFalse();
        verifyNoInteractions(analysisJobService);
        verify(ragBranchIndexRepository, never())
                .findActiveGenerationCoordinates(anyLong(), anyString());
        verify(incrementalRagUpdateService, never()).parseDiffForRag(anyString());
        verify(eventConsumer).accept(argThat(event -> "rag_skip".equals(event.get("state"))));
    }

    @Test
    void exactIncrementalCleanupClaimSkipsWithoutReadingOrMutatingGeneration() {
        service = new RagOperationsServiceImpl(
                ragIndexTrackingService, incrementalRagUpdateService,
                analysisLockService, analysisJobService,
                ragBranchIndexRepository, vcsClientProvider,
                ragPipelineClient, mock(RagBranchIndexRegistryService.class),
                mock(BranchIndexGenerationBuildService.class));
        setupRagEnabled();
        when(incrementalRagUpdateService.shouldPerformIncrementalUpdate(testProject))
                .thenReturn(true);
        when(analysisLockService.acquireLock(
                eq(testProject), eq("feature"), any(), eq("target-revision"), isNull()))
                .thenReturn(Optional.of("rag-lock"));
        when(ragBranchIndexRepository.existsByProjectIdAndBranchName(100L, "feature"))
                .thenReturn(true);
        when(ragBranchIndexRepository.markAccessedIfUnclaimed(
                eq(100L), eq("feature"), any()))
                .thenReturn(0);
        @SuppressWarnings("unchecked")
        Consumer<Map<String, Object>> eventConsumer = mock(Consumer.class);

        boolean result = service.triggerIncrementalUpdate(
                testProject, "feature", "target-revision", "caller diff", eventConsumer);

        assertThat(result).isFalse();
        verifyNoInteractions(analysisJobService);
        verify(ragBranchIndexRepository, never())
                .findActiveGenerationCoordinates(anyLong(), anyString());
        verify(incrementalRagUpdateService, never()).parseDiffForRag(anyString());
        verify(analysisLockService).releaseLock("rag-lock");
        verify(eventConsumer).accept(argThat(event -> "rag_skipped".equals(event.get("state"))));
    }

    @Test
    void exactIncrementalAlreadyAtTargetCompletesJobAndRepairsPrimaryCheckpoint()
            throws Exception {
        RagBranchIndexRegistryService registry = mock(RagBranchIndexRegistryService.class);
        BranchIndexGenerationBuildService builder = mock(BranchIndexGenerationBuildService.class);
        service = new RagOperationsServiceImpl(
                ragIndexTrackingService, incrementalRagUpdateService,
                analysisLockService, analysisJobService,
                ragBranchIndexRepository, vcsClientProvider,
                ragPipelineClient, registry, builder);
        setupRagEnabled();
        when(incrementalRagUpdateService.shouldPerformIncrementalUpdate(testProject))
                .thenReturn(true);
        when(analysisLockService.acquireLock(
                eq(testProject), eq("main"), any(), eq("target-revision"), isNull()))
                .thenReturn(Optional.of("rag-lock"));
        when(ragBranchIndexRepository.existsByProjectIdAndBranchName(100L, "main"))
                .thenReturn(true);
        when(ragBranchIndexRepository.markAccessedIfUnclaimed(
                eq(100L), eq("main"), any()))
                .thenReturn(1);
        RagBranchIndexRepository.ActiveGenerationCoordinates source =
                mock(RagBranchIndexRepository.ActiveGenerationCoordinates.class);
        OffsetDateTime activatedAt = OffsetDateTime.parse(
                "2026-08-17T15:27:17Z");
        when(source.getRevision()).thenReturn("target-revision");
        when(source.getActivatedAt()).thenReturn(activatedAt);
        when(ragBranchIndexRepository.findActiveGenerationCoordinates(100L, "main"))
                .thenReturn(Optional.of(source));
        @SuppressWarnings("unchecked")
        Consumer<Map<String, Object>> eventConsumer = mock(Consumer.class);

        boolean result = service.triggerIncrementalUpdate(
                testProject, "main", "target-revision", "stale caller diff", eventConsumer);

        assertThat(result).isTrue();
        verify(ragIndexTrackingService).preparePublishedGenerationForUpdate(
                testProject, "main", "target-revision", 0, 0, activatedAt);
        verifyNoInteractions(analysisJobService);
        verify(analysisLockService).releaseLock("rag-lock");
        verifyNoInteractions(vcsClientProvider, registry, builder);
        verify(incrementalRagUpdateService, never()).parseDiffForRag(anyString());
        verify(incrementalRagUpdateService, never()).performIncrementalUpdate(
                any(), any(), anyString(), anyString(), anyString(), anyString(),
                anySet(), anySet(), anySet(), anyString(), anyString(), anyString(),
                anyBoolean(), anyBoolean());
        verify(eventConsumer).accept(argThat(event ->
                "rag_complete".equals(event.get("state"))));
    }

    @Test
    void ensureExactMainAlwaysDelegatesToLockedTriggerWithoutPrediff() throws Exception {
        service = spy(new RagOperationsServiceImpl(
                ragIndexTrackingService, incrementalRagUpdateService,
                analysisLockService, analysisJobService,
                ragBranchIndexRepository, vcsClientProvider,
                ragPipelineClient, mock(RagBranchIndexRegistryService.class),
                mock(BranchIndexGenerationBuildService.class)));
        setupRagEnabled();
        setupVcsBinding();
        when(ragIndexTrackingService.isProjectIndexed(testProject)).thenReturn(true);
        VcsClient vcsClient = mock(VcsClient.class);
        when(vcsClientProvider.getClient(any(VcsConnection.class))).thenReturn(vcsClient);
        when(vcsClient.getLatestCommitHash("my-workspace", "my-repo", "main"))
                .thenReturn("main-head");
        doReturn(true).when(service).triggerIncrementalUpdate(
                eq(testProject), eq("main"), eq("main-head"), eq(""), any());
        @SuppressWarnings("unchecked")
        Consumer<Map<String, Object>> eventConsumer = mock(Consumer.class);

        boolean result = service.ensureRagIndexUpToDate(testProject, "main", eventConsumer);

        assertThat(result).isTrue();
        verify(service).triggerIncrementalUpdate(
                testProject, "main", "main-head", "", eventConsumer);
        verify(ragIndexTrackingService, never()).getIndexStatus(testProject);
        verify(vcsClient, never()).getBranchDiff(anyString(), anyString(), anyString(), anyString());
        verify(ragBranchIndexRepository, never())
                .findActiveGenerationCoordinates(anyLong(), anyString());
        verify(ragBranchIndexRepository, never())
                .findByProjectIdAndBranchName(anyLong(), anyString());
    }

    @Test
    void ensureExactBranchDelegatesMainAndBranchToLockedTriggersWithoutPrediff()
            throws Exception {
        service = spy(new RagOperationsServiceImpl(
                ragIndexTrackingService, incrementalRagUpdateService,
                analysisLockService, analysisJobService,
                ragBranchIndexRepository, vcsClientProvider,
                ragPipelineClient, mock(RagBranchIndexRegistryService.class),
                mock(BranchIndexGenerationBuildService.class)));
        setupRagEnabled();
        setupVcsBinding();
        when(ragIndexTrackingService.isProjectIndexed(testProject)).thenReturn(true);
        VcsClient vcsClient = mock(VcsClient.class);
        when(vcsClientProvider.getClient(any(VcsConnection.class))).thenReturn(vcsClient);
        when(vcsClient.getLatestCommitHash("my-workspace", "my-repo", "main"))
                .thenReturn("main-head");
        when(vcsClient.getLatestCommitHash("my-workspace", "my-repo", "feature"))
                .thenReturn("feature-head");
        doReturn(true).when(service).triggerIncrementalUpdate(
                eq(testProject), eq("main"), eq("main-head"), eq(""), any());
        doReturn(true).when(service).triggerIncrementalUpdate(
                eq(testProject), eq("feature"), eq("feature-head"), eq(""), any());
        @SuppressWarnings("unchecked")
        Consumer<Map<String, Object>> eventConsumer = mock(Consumer.class);

        boolean result = service.ensureRagIndexUpToDate(testProject, "feature", eventConsumer);

        assertThat(result).isTrue();
        verify(service).triggerIncrementalUpdate(
                testProject, "feature", "feature-head", "", eventConsumer);
        verify(service).triggerIncrementalUpdate(
                testProject, "main", "main-head", "", eventConsumer);
        verify(ragIndexTrackingService, never()).getIndexStatus(testProject);
        verify(vcsClient, never()).getBranchDiff(anyString(), anyString(), anyString(), anyString());
        verify(ragBranchIndexRepository, never())
                .findActiveGenerationCoordinates(anyLong(), anyString());
        verify(ragBranchIndexRepository, never())
                .findByProjectIdAndBranchName(anyLong(), anyString());
        verify(ragBranchIndexRepository, never()).save(any());
    }

    @Test
    void updateExactBranchDelegatesToLockedTriggerWithoutTrustingMutableCheckpoint()
            throws Exception {
        service = spy(new RagOperationsServiceImpl(
                ragIndexTrackingService, incrementalRagUpdateService,
                analysisLockService, analysisJobService,
                ragBranchIndexRepository, vcsClientProvider,
                ragPipelineClient, mock(RagBranchIndexRegistryService.class),
                mock(BranchIndexGenerationBuildService.class)));
        setupRagEnabled();
        setupVcsBinding();
        when(ragIndexTrackingService.isProjectIndexed(testProject)).thenReturn(true);
        VcsClient vcsClient = mock(VcsClient.class);
        when(vcsClientProvider.getClient(any(VcsConnection.class))).thenReturn(vcsClient);
        when(vcsClient.getLatestCommitHash("my-workspace", "my-repo", "feature"))
                .thenReturn("feature-head");
        doReturn(true).when(service).triggerIncrementalUpdate(
                eq(testProject), eq("feature"), eq("feature-head"), eq(""), any());
        @SuppressWarnings("unchecked")
        Consumer<Map<String, Object>> eventConsumer = mock(Consumer.class);

        boolean result = service.updateBranchIndex(testProject, "feature", eventConsumer);

        assertThat(result).isTrue();
        verify(service).triggerIncrementalUpdate(
                testProject, "feature", "feature-head", "", eventConsumer);
        verify(ragBranchIndexRepository, never())
                .findByProjectIdAndBranchName(anyLong(), anyString());
        verify(ragBranchIndexRepository, never())
                .findActiveGenerationCoordinates(anyLong(), anyString());
        verify(vcsClient, never()).getBranchDiff(anyString(), anyString(), anyString(), anyString());
    }

    @Test
    void testIsRagEnabled_NullConfig() {
        ReflectionTestUtils.setField(service, "ragApiEnabled", true);
        // testProject has no configuration set, so getConfiguration() returns null

        boolean result = service.isRagEnabled(testProject);

        assertThat(result).isFalse();
    }

    @Test
    void testIsRagEnabled_RagDisabledInConfig() {
        ReflectionTestUtils.setField(service, "ragApiEnabled", true);
        RagConfig ragConfig = new RagConfig(false);
        ProjectConfig config = new ProjectConfig(false, "main", null, ragConfig);
        testProject.setConfiguration(config);

        boolean result = service.isRagEnabled(testProject);

        assertThat(result).isFalse();
    }

    @Test
    void testIsRagEnabled_Success() {
        ReflectionTestUtils.setField(service, "ragApiEnabled", true);
        RagConfig ragConfig = new RagConfig(true);
        ProjectConfig config = new ProjectConfig(false, "main", null, ragConfig);
        testProject.setConfiguration(config);

        boolean result = service.isRagEnabled(testProject);

        assertThat(result).isTrue();
    }

    @Test
    void getBaseBranchPrefersExplicitRagBranch() {
        RagConfig ragConfig = new RagConfig(true, " release ");
        testProject.setConfiguration(
                new ProjectConfig(false, "configured", null, ragConfig));
        VcsRepoBinding binding = new VcsRepoBinding();
        binding.setDefaultBranch("provider-default");
        testProject.setVcsRepoBinding(binding);

        assertThat(service.getBaseBranch(testProject)).isEqualTo("release");
    }

    @Test
    void getBaseBranchUsesConfiguredProjectBranch() {
        testProject.setConfiguration(new ProjectConfig(false, "trunk"));

        assertThat(service.getBaseBranch(testProject)).isEqualTo("trunk");
    }

    @Test
    void getBaseBranchUsesRepositoryDefaultWhenConfigurationIsBlank() {
        testProject.setConfiguration(new ProjectConfig(false, "  "));
        VcsRepoBinding binding = new VcsRepoBinding();
        binding.setDefaultBranch("synthetic-target");
        testProject.setVcsRepoBinding(binding);

        assertThat(service.getBaseBranch(testProject))
                .isEqualTo("synthetic-target");
    }

    @Test
    void getBaseBranchUsesPersistedBranchWhenOtherSourcesAreAbsent() {
        Branch branch = new Branch();
        branch.setBranchName("stable");
        testProject.setDefaultBranch(branch);

        assertThat(service.getBaseBranch(testProject)).isEqualTo("stable");
    }

    @Test
    void getBaseBranchFailsWhenNoAuthoritativeIdentityExists() {
        assertThatThrownBy(() -> service.getBaseBranch(testProject))
                .isInstanceOf(IllegalStateException.class)
                .hasMessageContaining("No authoritative RAG base branch");
    }

    @Test
    void testIsRagIndexReady_RagNotEnabled() {
        ReflectionTestUtils.setField(service, "ragApiEnabled", false);

        boolean result = service.isRagIndexReady(testProject);

        assertThat(result).isFalse();
    }

    @Test
    void testIsRagIndexReady_ProjectNotIndexed() {
        ReflectionTestUtils.setField(service, "ragApiEnabled", true);
        RagConfig ragConfig = new RagConfig(true);
        ProjectConfig config = new ProjectConfig(false, "main", null, ragConfig);
        testProject.setConfiguration(config);
        when(ragIndexTrackingService.isProjectIndexed(testProject)).thenReturn(false);

        boolean result = service.isRagIndexReady(testProject);

        assertThat(result).isFalse();
    }

    @Test
    void testIsRagIndexReady_Success() {
        ReflectionTestUtils.setField(service, "ragApiEnabled", true);
        RagConfig ragConfig = new RagConfig(true);
        ProjectConfig config = new ProjectConfig(false, "main", null, ragConfig);
        testProject.setConfiguration(config);
        when(ragIndexTrackingService.isProjectIndexed(testProject)).thenReturn(true);

        boolean result = service.isRagIndexReady(testProject);

        assertThat(result).isTrue();
    }

    @Test
    void testIsBranchIndexReady_True() {
        when(ragBranchIndexRepository.existsByProjectIdAndBranchName(100L, "feature")).thenReturn(true);

        boolean result = service.isBranchIndexReady(testProject, "feature");

        assertThat(result).isTrue();
        verify(ragBranchIndexRepository).existsByProjectIdAndBranchName(100L, "feature");
    }

    @Test
    void testIsBranchIndexReady_False() {
        when(ragBranchIndexRepository.existsByProjectIdAndBranchName(100L, "feature")).thenReturn(false);

        boolean result = service.isBranchIndexReady(testProject, "feature");

        assertThat(result).isFalse();
    }

    @Test
    void testTriggerIncrementalUpdate_WhenNotEnabled() {
        ReflectionTestUtils.setField(service, "ragApiEnabled", false);
        @SuppressWarnings("unchecked")
        Consumer<Map<String, Object>> eventConsumer = mock(Consumer.class);

        boolean result =
                service.triggerIncrementalUpdate(testProject, "main", "abc123", "diff", eventConsumer);

        assertThat(result).isFalse();
        verifyNoInteractions(analysisJobService);
        verifyNoInteractions(analysisLockService);
    }

    @Test
    void testCreateOrUpdateBranchIndex_WhenNotEnabled() {
        ReflectionTestUtils.setField(service, "ragApiEnabled", false);
        @SuppressWarnings("unchecked")
        Consumer<Map<String, Object>> eventConsumer = mock(Consumer.class);

        service.createOrUpdateBranchIndex(testProject, "feature", "main", "commit123", "diff", eventConsumer);

        verifyNoInteractions(analysisJobService);
    }

    @Test
    void createOrUpdateBranchIndexRejectsBranchAnalysisPatternWithoutRetention() {
        ReflectionTestUtils.setField(service, "ragApiEnabled", true);
        testProject.setConfiguration(new ProjectConfig(
                false,
                "main",
                new BranchAnalysisConfig(List.of("main"), List.of("release/**")),
                new RagConfig(true, "main", null, null, true, 30, null, false)));
        service = spy(service);
        @SuppressWarnings("unchecked")
        Consumer<Map<String, Object>> eventConsumer = mock(Consumer.class);

        service.createOrUpdateBranchIndex(
                testProject,
                "release/preview",
                "main",
                "release-commit",
                "diff",
                eventConsumer);

        verify(service, never()).triggerIncrementalUpdate(any(), any(), any(), any(), any());
        verify(eventConsumer).accept(argThat(event ->
                "rag_skipped".equals(event.get("state"))));
    }

    @Test
    void testEnsureRagIndexUpToDate_WhenNotEnabled() {
        ReflectionTestUtils.setField(service, "ragApiEnabled", false);
        @SuppressWarnings("unchecked")
        Consumer<Map<String, Object>> eventConsumer = mock(Consumer.class);

        boolean result = service.ensureRagIndexUpToDate(testProject, "main", eventConsumer);

        assertThat(result).isFalse();
    }

    @Test
    void testEnsureBranchIndexForPrTarget_WhenNotEnabled() {
        ReflectionTestUtils.setField(service, "ragApiEnabled", false);
        @SuppressWarnings("unchecked")
        Consumer<Map<String, Object>> eventConsumer = mock(Consumer.class);

        boolean result = service.ensureBranchIndexForPrTarget(testProject, "feature", eventConsumer);

        assertThat(result).isFalse();
    }

    @Test
    void testConstructor() {
        assertThat(service).isNotNull();
    }

    // ── Additional coverage tests ───────────────────────────────────────────

    @Test
    void testGetDeletedFilesForBranch_WhenExists() {
        RagBranchIndex index = new RagBranchIndex();
        index.setDeletedFiles(java.util.Set.of("old.java", "removed.java"));
        when(ragBranchIndexRepository.findByProjectIdAndBranchName(100L, "feature"))
                .thenReturn(Optional.of(index));

        java.util.Set<String> result = service.getDeletedFilesForBranch(testProject, "feature");

        assertThat(result).containsExactlyInAnyOrder("old.java", "removed.java");
    }

    @Test
    void testGetDeletedFilesForBranch_WhenNotExists() {
        when(ragBranchIndexRepository.findByProjectIdAndBranchName(100L, "unknown"))
                .thenReturn(Optional.empty());

        java.util.Set<String> result = service.getDeletedFilesForBranch(testProject, "unknown");

        assertThat(result).isEmpty();
    }

    @Test
    void testDeleteBranchIndex_WhenNotEnabled() {
        ReflectionTestUtils.setField(service, "ragApiEnabled", false);
        @SuppressWarnings("unchecked")
        Consumer<Map<String, Object>> eventConsumer = mock(Consumer.class);

        boolean result = service.deleteBranchIndex(testProject, "feature", eventConsumer);

        assertThat(result).isFalse();
    }

    @Test
    void testDeleteBranchIndex_CannotDeleteMainBranch() {
        setupRagEnabled();
        @SuppressWarnings("unchecked")
        Consumer<Map<String, Object>> eventConsumer = mock(Consumer.class);

        boolean result = service.deleteBranchIndex(testProject, "main", eventConsumer);

        assertThat(result).isFalse();
        verify(eventConsumer).accept(argThat(m -> "warning".equals(m.get("type"))));
    }

    @Test
    void testDeleteBranchIndex_NoVcsBinding() {
        setupRagEnabled();
        @SuppressWarnings("unchecked")
        Consumer<Map<String, Object>> eventConsumer = mock(Consumer.class);

        boolean result = service.deleteBranchIndex(testProject, "feature", eventConsumer);

        assertThat(result).isFalse();
    }

    @Test
    void testDeleteBranchIndex_Success() throws Exception {
        setupRagEnabled();
        setupVcsBinding();
        when(ragPipelineClient.deleteBranchWithOutcome(
                "my-workspace", "my-repo", "feature", null))
                .thenReturn(RagPipelineClient.BranchDeletionOutcome.success("legacy-alias"));
        @SuppressWarnings("unchecked")
        Consumer<Map<String, Object>> eventConsumer = mock(Consumer.class);

        boolean result = service.deleteBranchIndex(testProject, "feature", eventConsumer);

        assertThat(result).isTrue();
        verify(ragBranchIndexRepository).deleteByProjectIdAndBranchName(100L, "feature");
    }

    @Test
    void testDeleteBranchIndex_PipelineFailure() throws Exception {
        setupRagEnabled();
        setupVcsBinding();
        when(ragPipelineClient.deleteBranchWithOutcome(
                "my-workspace", "my-repo", "feature", null))
                .thenReturn(RagPipelineClient.BranchDeletionOutcome.failure(
                        "legacy-alias", RagPipelineClient.BranchDeletionFailure.TARGET,
                        404, "not found"));
        @SuppressWarnings("unchecked")
        Consumer<Map<String, Object>> eventConsumer = mock(Consumer.class);

        boolean result = service.deleteBranchIndex(testProject, "feature", eventConsumer);

        assertThat(result).isFalse();
        verify(ragBranchIndexRepository, never()).deleteByProjectIdAndBranchName(anyLong(), anyString());
    }

    @Test
    void globalRagDisablementRetainsBranchWithoutCleanupWarning() {
        setupRagEnabled();
        setupVcsBinding();
        when(ragPipelineClient.deleteBranchWithOutcome(
                "my-workspace", "my-repo", "feature", null))
                .thenReturn(RagPipelineClient.BranchDeletionOutcome.failure(
                        "legacy-alias",
                        RagPipelineClient.BranchDeletionFailure.TARGET,
                        null,
                        "RAG disabled"));
        @SuppressWarnings("unchecked")
        Consumer<Map<String, Object>> eventConsumer = mock(Consumer.class);
        Logger logger = (Logger) LoggerFactory.getLogger(RagOperationsServiceImpl.class);
        ListAppender<ILoggingEvent> appender = new ListAppender<>();
        appender.start();
        logger.addAppender(appender);

        boolean result;
        try {
            result = service.deleteBranchIndex(testProject, "feature", eventConsumer);
        } finally {
            logger.detachAppender(appender);
        }

        assertThat(result).isFalse();
        verify(ragBranchIndexRepository, never())
                .deleteByProjectIdAndBranchName(anyLong(), anyString());
        assertThat(appender.list).noneMatch(event -> event.getLevel() == Level.WARN
                && event.getFormattedMessage().contains("delete branch RAG generation"));
    }

    @Test
    void exactDeletionKeepsActiveTargetWhenOlderTargetIsRejected() {
        setupRagEnabled();
        setupVcsBinding();
        setupProjectWithWorkspaceAndNamespace();
        RagBranchIndex branchIndex = new RagBranchIndex(
                testProject, "feature", RagBranchIndexKind.DURABLE);
        branchIndex.setId(501L);
        RagBranchIndexGeneration rejected = new RagBranchIndexGeneration();
        rejected.setCollectionName("superseded-target");
        rejected.setRevision("revision-1");
        rejected.activate("manifest-1", 1, 1);
        rejected.supersede();
        RagBranchIndexGeneration active = new RagBranchIndexGeneration();
        active.setCollectionName("active-target");
        active.setRevision("revision-2");
        active.activate("manifest-2", 1, 1);
        when(ragBranchIndexRepository.findByProjectIdAndBranchName(100L, "feature"))
                .thenReturn(Optional.of(branchIndex));
        when(branchGenerationRepository.findByBranchIndexIdOrderByCreatedAtDesc(501L))
                .thenReturn(List.of(active, rejected));
        when(ragPipelineClient.deleteBranchWithOutcome(
                "test-ws", "test-ns", "feature", "superseded-target",
                "revision-1", "manifest-1"))
                .thenReturn(RagPipelineClient.BranchDeletionOutcome.failure(
                        "superseded-target",
                        RagPipelineClient.BranchDeletionFailure.TARGET,
                        422,
                        "manifest receipt rejected"));
        @SuppressWarnings("unchecked")
        Consumer<Map<String, Object>> eventConsumer = mock(Consumer.class);

        boolean result = service.deleteBranchIndex(testProject, "feature", eventConsumer);

        assertThat(result).isFalse();
        verify(ragPipelineClient, never()).deleteBranchWithOutcome(
                "test-ws", "test-ns", "feature", "active-target",
                "revision-2", "manifest-2");
        verify(ragBranchIndexRepository, never())
                .deleteByProjectIdAndBranchName(anyLong(), anyString());
    }

    @Test
    void testDeleteBranchIndex_PipelineException() throws Exception {
        setupRagEnabled();
        setupVcsBinding();
        when(ragPipelineClient.deleteBranchWithOutcome(
                "my-workspace", "my-repo", "feature", null))
                .thenThrow(new RuntimeException("Connection timeout"));
        @SuppressWarnings("unchecked")
        Consumer<Map<String, Object>> eventConsumer = mock(Consumer.class);

        boolean result = service.deleteBranchIndex(testProject, "feature", eventConsumer);

        assertThat(result).isFalse();
    }

    @Test
    void testCleanupStaleBranches_WhenNotEnabled() {
        ReflectionTestUtils.setField(service, "ragApiEnabled", false);
        @SuppressWarnings("unchecked")
        Consumer<Map<String, Object>> eventConsumer = mock(Consumer.class);

        Map<String, Object> result = service.cleanupStaleBranches(testProject, java.util.Set.of("feature"),
                eventConsumer);

        assertThat(result).containsEntry("status", "skipped");
    }

    @Test
    void testCleanupStaleBranches_NoVcsBinding() {
        setupRagEnabled();
        @SuppressWarnings("unchecked")
        Consumer<Map<String, Object>> eventConsumer = mock(Consumer.class);

        Map<String, Object> result = service.cleanupStaleBranches(testProject, java.util.Set.of("feature"),
                eventConsumer);

        assertThat(result).containsEntry("status", "error");
    }

    @Test
    void testCleanupStaleBranches_NoStaleBranches() {
        setupRagEnabled();
        setupVcsBinding();
        when(ragPipelineClient.getIndexedBranches("my-workspace", "my-repo"))
                .thenReturn(java.util.List.of("main", "feature"));
        @SuppressWarnings("unchecked")
        Consumer<Map<String, Object>> eventConsumer = mock(Consumer.class);

        Map<String, Object> result = service.cleanupStaleBranches(testProject, java.util.Set.of("feature"),
                eventConsumer);

        assertThat(result).containsEntry("status", "success");
        assertThat(result).containsEntry("total_deleted", 0);
    }

    @Test
    void testCleanupStaleBranches_DeletesStaleBranch() throws Exception {
        setupRagEnabled();
        setupVcsBinding();
        when(ragPipelineClient.getIndexedBranches("my-workspace", "my-repo"))
                .thenReturn(java.util.List.of("main", "feature", "stale-branch"));
        when(ragPipelineClient.deleteBranchWithOutcome(
                "my-workspace", "my-repo", "stale-branch", null))
                .thenReturn(RagPipelineClient.BranchDeletionOutcome.success("legacy-alias"));
        @SuppressWarnings("unchecked")
        Consumer<Map<String, Object>> eventConsumer = mock(Consumer.class);

        Map<String, Object> result = service.cleanupStaleBranches(testProject, java.util.Set.of("feature"),
                eventConsumer);

        assertThat(result).containsEntry("status", "success");
        assertThat(result).containsEntry("total_deleted", 1);
    }

    @Test
    void cleanupStaleBranchesDeletesEveryRegisteredExactGeneration() throws Exception {
        setupRagEnabled();
        setupVcsBinding();
        setupProjectWithWorkspaceAndNamespace();
        when(ragBranchIndexRepository.findBranchNamesByProjectId(100L))
                .thenReturn(List.of("main", "stale-exact"));
        when(ragPipelineClient.getIndexedBranches("my-workspace", "my-repo"))
                .thenReturn(List.of("main"));

        RagBranchIndex branchIndex = new RagBranchIndex(
                testProject, "stale-exact", RagBranchIndexKind.DURABLE);
        branchIndex.setId(501L);
        RagBranchIndexGeneration first = new RagBranchIndexGeneration();
        first.setCollectionName("cc_generation_1");
        first.setRevision("revision-1");
        first.setManifestDigest("manifest-1");
        RagBranchIndexGeneration second = new RagBranchIndexGeneration();
        second.setCollectionName("cc_generation_2");
        second.setRevision("revision-2");
        second.setManifestDigest("manifest-2");
        when(ragBranchIndexRepository.findByProjectIdAndBranchName(100L, "stale-exact"))
                .thenReturn(Optional.of(branchIndex));
        when(branchGenerationRepository.findByBranchIndexIdOrderByCreatedAtDesc(501L))
                .thenReturn(List.of(first, second));
        when(ragPipelineClient.deleteBranchWithOutcome(
                "test-ws", "test-ns", "stale-exact", "cc_generation_1",
                "revision-1", "manifest-1"))
                .thenReturn(RagPipelineClient.BranchDeletionOutcome.success("cc_generation_1"));
        when(ragPipelineClient.deleteBranchWithOutcome(
                "test-ws", "test-ns", "stale-exact", "cc_generation_2",
                "revision-2", "manifest-2"))
                .thenReturn(RagPipelineClient.BranchDeletionOutcome.success("cc_generation_2"));
        @SuppressWarnings("unchecked")
        Consumer<Map<String, Object>> eventConsumer = mock(Consumer.class);

        Map<String, Object> result = service.cleanupStaleBranches(
                testProject, Set.of(), eventConsumer);

        assertThat(result).containsEntry("status", "success");
        assertThat(result).containsEntry("total_deleted", 1);
        assertThat(result.get("deleted_branches")).isEqualTo(List.of("stale-exact"));
        verify(ragPipelineClient).deleteBranchWithOutcome(
                "test-ws", "test-ns", "stale-exact", "cc_generation_1",
                "revision-1", "manifest-1");
        verify(ragPipelineClient).deleteBranchWithOutcome(
                "test-ws", "test-ns", "stale-exact", "cc_generation_2",
                "revision-2", "manifest-2");
        verify(ragBranchIndexRepository)
                .deleteByProjectIdAndBranchName(100L, "stale-exact");
        verify(ragPipelineClient, never()).deleteBranchWithOutcome(
                "my-workspace", "my-repo", "stale-exact", null);
    }

    @Test
    void testUpdateBranchIndex_WhenNotEnabled() {
        ReflectionTestUtils.setField(service, "ragApiEnabled", false);
        @SuppressWarnings("unchecked")
        Consumer<Map<String, Object>> eventConsumer = mock(Consumer.class);

        boolean result = service.updateBranchIndex(testProject, "feature", eventConsumer);

        assertThat(result).isFalse();
    }

    @Test
    void testUpdateBranchIndex_IndexNotReady() {
        setupRagEnabled();
        when(ragIndexTrackingService.isProjectIndexed(testProject)).thenReturn(false);
        @SuppressWarnings("unchecked")
        Consumer<Map<String, Object>> eventConsumer = mock(Consumer.class);

        boolean result = service.updateBranchIndex(testProject, "feature", eventConsumer);

        assertThat(result).isFalse();
    }

    @Test
    void testUpdateBranchIndex_NoVcsBinding() {
        setupRagEnabled();
        when(ragIndexTrackingService.isProjectIndexed(testProject)).thenReturn(true);
        @SuppressWarnings("unchecked")
        Consumer<Map<String, Object>> eventConsumer = mock(Consumer.class);

        boolean result = service.updateBranchIndex(testProject, "feature", eventConsumer);

        assertThat(result).isFalse();
    }

    @Test
    void testUpdateBranchIndex_SameBranchAsBase() {
        setupRagEnabled();
        setupVcsBinding();
        when(ragIndexTrackingService.isProjectIndexed(testProject)).thenReturn(true);
        @SuppressWarnings("unchecked")
        Consumer<Map<String, Object>> eventConsumer = mock(Consumer.class);

        // "main" is the base branch
        boolean result = service.updateBranchIndex(testProject, "main", eventConsumer);

        assertThat(result).isTrue();
    }

    @Test
    void testEnsureBranchIndexForPrTarget_NotEnabled() {
        ReflectionTestUtils.setField(service, "ragApiEnabled", false);
        @SuppressWarnings("unchecked")
        Consumer<Map<String, Object>> eventConsumer = mock(Consumer.class);

        boolean result = service.ensureBranchIndexForPrTarget(testProject, "feature", eventConsumer);

        assertThat(result).isFalse();
    }

    @Test
    void testEnsureBranchIndexForPrTarget_IndexNotReady() {
        setupRagEnabled();
        when(ragIndexTrackingService.isProjectIndexed(testProject)).thenReturn(false);
        @SuppressWarnings("unchecked")
        Consumer<Map<String, Object>> eventConsumer = mock(Consumer.class);

        boolean result = service.ensureBranchIndexForPrTarget(testProject, "feature", eventConsumer);

        assertThat(result).isFalse();
    }

    @Test
    void testEnsureBranchIndexForPrTarget_NoVcsBinding() {
        setupRagEnabled();
        when(ragIndexTrackingService.isProjectIndexed(testProject)).thenReturn(true);
        @SuppressWarnings("unchecked")
        Consumer<Map<String, Object>> eventConsumer = mock(Consumer.class);

        boolean result = service.ensureBranchIndexForPrTarget(testProject, "feature", eventConsumer);

        assertThat(result).isFalse();
    }

    @Test
    void testEnsureBranchIndexForPrTarget_SameBranch() {
        setupRagEnabled();
        setupVcsBinding();
        when(ragIndexTrackingService.isProjectIndexed(testProject)).thenReturn(true);
        @SuppressWarnings("unchecked")
        Consumer<Map<String, Object>> eventConsumer = mock(Consumer.class);

        boolean result = service.ensureBranchIndexForPrTarget(testProject, "main", eventConsumer);

        assertThat(result).isTrue();
    }

    @Test
    void testEnsureRagIndexUpToDate_NoVcsBinding() {
        setupRagEnabled();
        @SuppressWarnings("unchecked")
        Consumer<Map<String, Object>> eventConsumer = mock(Consumer.class);

        boolean result = service.ensureRagIndexUpToDate(testProject, "main", eventConsumer);

        assertThat(result).isFalse();
    }

    // ── triggerIncrementalUpdate full-flow tests ──────────────────────────

    @Test
    void testTriggerIncrementalUpdate_FullSuccessFlow() throws Exception {
        setupRagEnabled();
        setupVcsBinding();
        @SuppressWarnings("unchecked")
        Consumer<Map<String, Object>> eventConsumer = mock(Consumer.class);
        Job mockJob = mock(Job.class);
        when(mockJob.getId()).thenReturn(77L);

        when(incrementalRagUpdateService.shouldPerformIncrementalUpdate(testProject)).thenReturn(true);
        when(incrementalRagUpdateService.parseDiffForRag("diff content"))
                .thenReturn(new IncrementalRagUpdateService.DiffResult(Set.of("src/A.java"),
                        java.util.Collections.<String>emptySet(), Set.of("src/B.java")));
        when(analysisJobService.createRagIndexJob(
                any(), anyBoolean(), any(), anyString(), anyString())).thenReturn(mockJob);
        when(analysisLockService.acquireLock(any(), eq("feature"), any(), eq("commit1"), isNull()))
                .thenReturn(Optional.of("lock-key"));
        when(incrementalRagUpdateService.performIncrementalUpdate(
                any(), any(), anyString(), anyString(), anyString(), anyString(), any(),
                any(), any()))
                .thenReturn(Map.of("updatedFiles", 1, "deletedFiles", 1));
        when(ragBranchIndexRepository.findByProjectIdAndBranchName(100L, "feature"))
                .thenReturn(Optional.empty());

        boolean result = service.triggerIncrementalUpdate(
                testProject, "feature", "commit1", "diff content", eventConsumer);

        assertThat(result).isTrue();
        verifyNoInteractions(ragIndexTrackingService);
        verify(analysisLockService).releaseLock("lock-key");
        verify(analysisJobService, never()).completeJob(eq(mockJob), isNull());
        verify(analysisJobService).recordExternallyCompletedJob(
                eq(mockJob), eq("rag_complete"), contains("RAG index updated"));
        verify(legacyRagUpdateCompletionService).complete(
                eq(testProject), eq("feature"), eq("commit1"), eq(77L),
                any(), eq(false), eq(0), eq(1), isNull(), eq(Set.of("src/B.java")));
        verify(legacyRagJobLeaseService).start(mockJob.getId());
        verify(analysisLockService).maintainLockLease("lock-key", 360);
        verify(legacyJobLease).confirmOwnership();
        verify(legacyLockLease).confirmOwnership();
        verify(legacyJobLease).close();
        verify(legacyLockLease).close();
    }

    @Test
    void legacyOwnershipLossBeforeRemoteWorkDoesNotMutateOrOverwriteRecovery()
            throws Exception {
        setupRagEnabled();
        Job job = mock(Job.class);
        when(job.getId()).thenReturn(77L);
        when(incrementalRagUpdateService.shouldPerformIncrementalUpdate(testProject))
                .thenReturn(true);
        when(incrementalRagUpdateService.parseDiffForRag("diff"))
                .thenReturn(new IncrementalRagUpdateService.DiffResult(
                        Set.of("src/A.java"), Set.of(), Set.of()));
        when(analysisJobService.createRagIndexJob(
                any(), anyBoolean(), any(), anyString(), anyString()))
                .thenReturn(job);
        when(analysisLockService.acquireLock(
                any(), eq("feature"), any(), eq("commit1"), isNull()))
                .thenReturn(Optional.of("lock-key"));
        when(legacyJobLease.isOwnershipLost()).thenReturn(true);
        @SuppressWarnings("unchecked")
        Consumer<Map<String, Object>> events = mock(Consumer.class);

        boolean result = service.triggerIncrementalUpdate(
                testProject, "feature", "commit1", "diff", events);

        assertThat(result).isFalse();
        verify(incrementalRagUpdateService, never()).performIncrementalUpdate(
                any(), any(), anyString(), anyString(), anyString(), anyString(),
                any(), any(), any());
        verify(analysisJobService, never()).failJob(any(), anyString());
        verifyNoInteractions(ragIndexTrackingService);
        verify(legacyJobLease).close();
        verify(legacyLockLease).close();
        verify(analysisLockService).releaseLock("lock-key");
        verify(events).accept(argThat(event ->
                "rag_error".equals(event.get("state"))
                        && String.valueOf(event.get("message"))
                            .contains("lost durable ownership")));
    }

    @Test
    void legacyOwnershipIsReconfirmedBeforeCheckpointAndJobCompletion()
            throws Exception {
        setupRagEnabled();
        setupVcsBinding();
        Job job = mock(Job.class);
        when(job.getId()).thenReturn(77L);
        when(incrementalRagUpdateService.shouldPerformIncrementalUpdate(testProject))
                .thenReturn(true);
        when(incrementalRagUpdateService.parseDiffForRag("diff"))
                .thenReturn(new IncrementalRagUpdateService.DiffResult(
                        Set.of("src/A.java"), Set.of(), Set.of()));
        when(analysisJobService.createRagIndexJob(
                any(), anyBoolean(), any(), anyString(), anyString()))
                .thenReturn(job);
        when(analysisLockService.acquireLock(
                any(), eq("main"), any(), eq("commit1"), isNull()))
                .thenReturn(Optional.of("lock-key"));
        when(incrementalRagUpdateService.performIncrementalUpdate(
                any(), any(), anyString(), anyString(), anyString(), anyString(),
                any(), any(), any()))
                .thenReturn(Map.of("updatedFiles", 1, "deletedFiles", 0));
        when(legacyLockLease.confirmOwnership()).thenReturn(false);

        boolean result = service.triggerIncrementalUpdate(
                testProject, "main", "commit1", "diff", ignored -> { });

        assertThat(result).isFalse();
        verify(incrementalRagUpdateService).performIncrementalUpdate(
                any(), any(), anyString(), anyString(), eq("main"), eq("commit1"),
                any(), any(), any());
        verify(legacyJobLease).confirmOwnership();
        verify(legacyLockLease).confirmOwnership();
        verify(ragIndexTrackingService, never()).markUpdatingCompleted(
                any(), anyString(), anyString(), any(), any(), any(), any());
        verify(legacyRagUpdateCompletionService, never()).complete(
                any(), anyString(), anyString(), anyLong(), any(), anyBoolean(),
                anyInt(), anyInt(), any(), anySet());
        verify(analysisJobService, never()).completeJob(any(), any());
        verify(analysisJobService, never()).failJob(any(), anyString());
        verify(ragIndexTrackingService, never()).markIncrementalUpdateFailed(
                any(), anyString(), any());
        verify(legacyJobLease).close();
        verify(legacyLockLease).close();
    }

    @Test
    void newerCommitWithEmptyRangeCreatesJobAndAdvancesCheckpoint() throws Exception {
        setupRagEnabled();
        setupVcsBinding();
        @SuppressWarnings("unchecked")
        Consumer<Map<String, Object>> eventConsumer = mock(Consumer.class);
        Job job = mock(Job.class);
        when(job.getId()).thenReturn(82L);

        when(incrementalRagUpdateService.shouldPerformIncrementalUpdate(testProject)).thenReturn(true);
        when(incrementalRagUpdateService.parseDiffForRag(""))
                .thenReturn(new IncrementalRagUpdateService.DiffResult(
                        Set.of(), Set.of(), Set.of()));
        when(analysisJobService.createRagIndexJob(
                testProject, false, JobTriggerSource.WEBHOOK, "main", "abc"))
                .thenReturn(job);
        when(analysisLockService.acquireLock(
                any(), eq("main"), any(), eq("abc"), isNull()))
                .thenReturn(Optional.of("lock-key"));
        when(incrementalRagUpdateService.performIncrementalUpdate(
                any(), any(), anyString(), anyString(), anyString(), anyString(),
                anySet(), anySet(), anySet()))
                .thenReturn(Map.of("status", "completed"));

        boolean result =
                service.triggerIncrementalUpdate(testProject, "main", "abc", "", eventConsumer);

        assertThat(result).isTrue();
        verify(analysisJobService).startJob(job);
        verify(analysisLockService).acquireLock(
                any(), eq("main"), any(), eq("abc"), isNull());
        verify(ragIndexTrackingService).markUpdatingStarted(
                testProject, "main", "abc", 82L);
        verify(legacyRagUpdateCompletionService).complete(
                eq(testProject), eq("main"), eq("abc"), eq(82L),
                any(), eq(true), eq(0), eq(0), isNull(), eq(Set.of()));
        verify(analysisJobService).recordExternallyCompletedJob(
                eq(job), eq("rag_complete"), contains("checkpoint advanced"));
        verify(eventConsumer).accept(argThat(event ->
                "rag_complete".equals(event.get("state"))
                        && String.valueOf(event.get("message"))
                            .contains("checkpoint advanced")));
    }

    @Test
    void alreadyCurrentLegacyCommitDoesNotCreateAJobOrClaimAnUpdate() {
        setupRagEnabled();
        RagIndexStatus completedStatus = new RagIndexStatus();
        completedStatus.setIndexedCommitHash("abc");
        when(incrementalRagUpdateService.shouldPerformIncrementalUpdate(testProject))
                .thenReturn(true);
        when(ragIndexTrackingService.getIndexStatus(testProject))
                .thenReturn(Optional.of(completedStatus));
        @SuppressWarnings("unchecked")
        Consumer<Map<String, Object>> eventConsumer = mock(Consumer.class);

        boolean result = service.triggerIncrementalUpdate(
                testProject, "main", "abc", "stale caller diff", eventConsumer);

        assertThat(result).isTrue();
        verifyNoInteractions(analysisJobService, analysisLockService, vcsClientProvider);
        verify(incrementalRagUpdateService, never()).parseDiffForRag(anyString());
        verify(eventConsumer).accept(argThat(event ->
                "rag_current".equals(event.get("state"))
                        && String.valueOf(event.get("message"))
                            .contains("already represents")));
    }

    @Test
    void nonEmptyUnrecognizedDiffCreatesFailedJobAndRetainsCheckpoint() {
        setupRagEnabled();
        Job job = mock(Job.class);
        when(incrementalRagUpdateService.shouldPerformIncrementalUpdate(testProject))
                .thenReturn(true);
        when(incrementalRagUpdateService.parseDiffForRag("provider payload"))
                .thenReturn(new IncrementalRagUpdateService.DiffResult(
                        Set.of(), Set.of(), Set.of()));
        when(analysisJobService.createRagIndexJob(
                testProject, false, JobTriggerSource.WEBHOOK, "main", "abc"))
                .thenReturn(job);
        @SuppressWarnings("unchecked")
        Consumer<Map<String, Object>> eventConsumer = mock(Consumer.class);

        boolean result = service.triggerIncrementalUpdate(
                testProject, "main", "abc", "provider payload", eventConsumer);

        assertThat(result).isFalse();
        verify(analysisJobService).startJob(job);
        verify(analysisJobService).failJob(
                eq(job), contains("no recognizable file changes"));
        verifyNoInteractions(analysisLockService, legacyRagUpdateCompletionService);
        verify(eventConsumer).accept(argThat(event ->
                "rag_error".equals(event.get("state"))));
    }

    @Test
    void testTriggerIncrementalUpdate_LockNotAcquired() {
        setupRagEnabled();
        @SuppressWarnings("unchecked")
        Consumer<Map<String, Object>> eventConsumer = mock(Consumer.class);
        Job mockJob = mock(Job.class);

        when(incrementalRagUpdateService.shouldPerformIncrementalUpdate(testProject)).thenReturn(true);
        when(incrementalRagUpdateService.parseDiffForRag(anyString()))
                .thenReturn(new IncrementalRagUpdateService.DiffResult(Set.of("a.java"),
                        java.util.Collections.<String>emptySet(), java.util.Collections.<String>emptySet()));
        when(analysisJobService.createRagIndexJob(
                any(), anyBoolean(), any(), anyString(), anyString())).thenReturn(mockJob);
        when(analysisLockService.acquireLock(any(), anyString(), any(), anyString(), isNull()))
                .thenReturn(Optional.empty());

        boolean result =
                service.triggerIncrementalUpdate(testProject, "feature", "c1", "diff", eventConsumer);

        assertThat(result).isFalse();
        verify(analysisJobService).skipJob(
                eq(mockJob), contains("previous checkpoint is retained for the next trigger"));
        verify(analysisJobService, never()).failJob(any(), anyString());
        verify(eventConsumer).accept(argThat(event ->
                "rag_skip".equals(event.get("state"))
                        && String.valueOf(event.get("message")).contains("next trigger")));
        verifyNoInteractions(ragIndexTrackingService);
    }

    @Test
    void lockContentionObserverFailureCannotChangeSkippedTerminalState() {
        setupRagEnabled();
        @SuppressWarnings("unchecked")
        Consumer<Map<String, Object>> eventConsumer = mock(Consumer.class);
        Job mockJob = mock(Job.class);
        when(incrementalRagUpdateService.shouldPerformIncrementalUpdate(testProject)).thenReturn(true);
        when(incrementalRagUpdateService.parseDiffForRag(anyString()))
                .thenReturn(new IncrementalRagUpdateService.DiffResult(
                        Set.of("a.java"), Set.of(), Set.of()));
        when(analysisJobService.createRagIndexJob(
                any(), anyBoolean(), any(), anyString(), anyString())).thenReturn(mockJob);
        when(analysisLockService.acquireLock(any(), anyString(), any(), anyString(), isNull()))
                .thenReturn(Optional.empty());
        doThrow(new IllegalStateException("observer disconnected"))
                .when(eventConsumer).accept(anyMap());

        boolean result = service.triggerIncrementalUpdate(
                testProject, "feature", "c1", "diff", eventConsumer);

        assertThat(result).isFalse();
        verify(analysisJobService).skipJob(eq(mockJob), anyString());
        verify(analysisJobService, never()).failJob(any(), anyString());
    }

    @Test
    void testTriggerIncrementalUpdate_IncrementalUpdateThrows() throws Exception {
        setupRagEnabled();
        setupVcsBinding();
        @SuppressWarnings("unchecked")
        Consumer<Map<String, Object>> eventConsumer = mock(Consumer.class);
        Job mockJob = mock(Job.class);
        when(mockJob.getId()).thenReturn(78L);

        when(incrementalRagUpdateService.shouldPerformIncrementalUpdate(testProject)).thenReturn(true);
        when(incrementalRagUpdateService.parseDiffForRag(anyString()))
                .thenReturn(new IncrementalRagUpdateService.DiffResult(Set.of("a.java"),
                        java.util.Collections.<String>emptySet(), java.util.Collections.<String>emptySet()));
        when(analysisJobService.createRagIndexJob(
                any(), anyBoolean(), any(), anyString(), anyString())).thenReturn(mockJob);
        when(analysisLockService.acquireLock(any(), anyString(), any(), anyString(), isNull()))
                .thenReturn(Optional.of("lock-key"));
        when(incrementalRagUpdateService.performIncrementalUpdate(
                any(), any(), anyString(), anyString(), anyString(), anyString(), any(),
                any(), any()))
                .thenThrow(new RuntimeException("Pipeline down"));

        boolean result =
                service.triggerIncrementalUpdate(testProject, "feature", "c1", "diff", eventConsumer);

        assertThat(result).isFalse();
        // A retained branch has its own durable operation state and cannot
        // overwrite the primary branch's project-level status.
        verifyNoInteractions(ragIndexTrackingService);
        verify(analysisJobService).failJob(
                eq(mockJob), contains("RAG incremental update failed: Pipeline down"));
        verify(analysisJobService, never()).error(eq(mockJob), eq("rag_error"), anyString());
        verify(eventConsumer).accept(argThat(event ->
                "rag_error".equals(event.get("state"))
                        && "warning".equals(event.get("type"))));
        verify(analysisLockService).releaseLock("lock-key");
    }

    @Test
    void testTriggerIncrementalUpdate_UsesCompletedMainCheckpointAfterEarlierFailure()
            throws Exception {
        setupRagEnabled();
        setupVcsBinding();
        @SuppressWarnings("unchecked")
        Consumer<Map<String, Object>> eventConsumer = mock(Consumer.class);
        Job mockJob = mock(Job.class);
        when(mockJob.getId()).thenReturn(79L);
        VcsClient vcsClient = mock(VcsClient.class);
        RagIndexStatus completedStatus = new RagIndexStatus();
        completedStatus.setIndexedBranch("main");
        completedStatus.setIndexedCommitHash("last-completed");
        String catchUpDiff =
                "diff --git a/src/Recovered.java b/src/Recovered.java\n+recovered\n";

        when(incrementalRagUpdateService.shouldPerformIncrementalUpdate(testProject))
                .thenReturn(true);
        when(ragIndexTrackingService.getIndexStatus(testProject))
                .thenReturn(Optional.of(completedStatus));
        when(vcsClientProvider.getClient(any(VcsConnection.class))).thenReturn(vcsClient);
        when(vcsClient.getBranchDiff(
                "my-workspace", "my-repo", "last-completed", "current-head"))
                .thenReturn(catchUpDiff);
        when(incrementalRagUpdateService.parseDiffForRag(catchUpDiff))
                .thenReturn(new IncrementalRagUpdateService.DiffResult(
                        Set.of("src/Recovered.java"), Set.of(), Set.of()));
        when(analysisJobService.createRagIndexJob(
                any(), anyBoolean(), any(), anyString(), anyString()))
                .thenReturn(mockJob);
        when(analysisLockService.acquireLock(
                any(), eq("main"), any(), eq("current-head"), isNull()))
                .thenReturn(Optional.of("lock-key"));
        when(incrementalRagUpdateService.performIncrementalUpdate(
                any(), any(), anyString(), anyString(), anyString(), anyString(), any(),
                any(), any()))
                .thenReturn(Map.of("updatedFiles", 1, "deletedFiles", 0));
        boolean result = service.triggerIncrementalUpdate(
                testProject, "main", "current-head",
                "diff --git a/src/OnlyLatest.java b/src/OnlyLatest.java\n+latest\n",
                eventConsumer);

        assertThat(result).isTrue();
        verify(incrementalRagUpdateService).parseDiffForRag(catchUpDiff);
        verify(incrementalRagUpdateService).performIncrementalUpdate(
                eq(testProject), any(VcsConnection.class), eq("my-workspace"), eq("my-repo"),
                eq("main"), eq("current-head"), eq(Set.of("src/Recovered.java")),
                eq(Set.of()), eq(Set.of()));
        verify(legacyRagUpdateCompletionService).complete(
                eq(testProject), eq("main"), eq("current-head"), eq(79L),
                any(), eq(true), eq(0), eq(0), isNull(), eq(Set.of()));
    }

    @Test
    void testTriggerIncrementalUpdate_TrackBranchIndex_MergesDeletedFiles() throws Exception {
        setupRagEnabled();
        setupVcsBinding();
        @SuppressWarnings("unchecked")
        Consumer<Map<String, Object>> eventConsumer = mock(Consumer.class);
        Job mockJob = mock(Job.class);
        when(mockJob.getId()).thenReturn(80L);

        when(incrementalRagUpdateService.shouldPerformIncrementalUpdate(testProject)).thenReturn(true);
        when(incrementalRagUpdateService.parseDiffForRag(anyString()))
                .thenReturn(new IncrementalRagUpdateService.DiffResult(java.util.Collections.<String>emptySet(),
                        java.util.Collections.<String>emptySet(), Set.of("old.java")));
        when(analysisJobService.createRagIndexJob(
                any(), anyBoolean(), any(), anyString(), anyString())).thenReturn(mockJob);
        when(analysisLockService.acquireLock(any(), anyString(), any(), anyString(), isNull()))
                .thenReturn(Optional.of("lock-key"));
        when(incrementalRagUpdateService.performIncrementalUpdate(
                any(), any(), anyString(), anyString(), anyString(), anyString(), any(),
                any(), any()))
                .thenReturn(Map.of("deletedFiles", 1));

        // Existing branch index with existing deleted files
        RagBranchIndex existingIndex = new RagBranchIndex();
        existingIndex.setDeletedFiles(new java.util.HashSet<>(Set.of("prev.java")));
        when(ragBranchIndexRepository.findByProjectIdAndBranchName(100L, "feature"))
                .thenReturn(Optional.of(existingIndex));

        service.triggerIncrementalUpdate(testProject, "feature", "c1", "diff", eventConsumer);

        verify(legacyRagUpdateCompletionService).complete(
                eq(testProject), eq("feature"), eq("c1"), eq(80L),
                any(), eq(false), eq(0), eq(1), isNull(), eq(Set.of("old.java")));
    }

    // ── updateBranchIndex full-flow tests ───────────────────────────────

    @Test
    void testUpdateBranchIndex_SuccessWithDiff() throws Exception {
        setupRagEnabled();
        setupVcsBinding();
        when(ragIndexTrackingService.isProjectIndexed(testProject)).thenReturn(true);
        VcsClient mockVcs = mock(VcsClient.class);
        when(vcsClientProvider.getClient(any(VcsConnection.class))).thenReturn(mockVcs);
        when(mockVcs.getBranchDiff("my-workspace", "my-repo", "main", "feature")).thenReturn("diff data");
        when(mockVcs.getLatestCommitHash("my-workspace", "my-repo", "feature")).thenReturn("abc123");
        stubSuccessfulIncrementalUpdate("feature", "abc123");
        @SuppressWarnings("unchecked")
        Consumer<Map<String, Object>> eventConsumer = mock(Consumer.class);

        boolean result = service.updateBranchIndex(testProject, "feature", eventConsumer);

        assertThat(result).isTrue();
        verify(mockVcs).getBranchDiff("my-workspace", "my-repo", "main", "feature");
    }

    @Test
    void updateBranchIndexUsesCompletedBranchCheckpointWithoutComparingMain() throws Exception {
        setupRagEnabled();
        setupVcsBinding();
        setupProjectWithWorkspaceAndNamespace();
        when(ragIndexTrackingService.isProjectIndexed(testProject)).thenReturn(true);
        when(incrementalRagUpdateService.shouldPerformIncrementalUpdate(testProject)).thenReturn(true);
        when(incrementalRagUpdateService.parseDiffForRag("checkpoint diff"))
                .thenReturn(new IncrementalRagUpdateService.DiffResult(
                        Set.of(), Set.of("src/Changed.java"), Set.of()));
        when(analysisJobService.createRagIndexJob(
                eq(testProject), eq(false), any(), anyString(), anyString())).thenReturn(mock(Job.class));
        when(analysisLockService.acquireLock(any(), anyString(), any(), anyString(), isNull()))
                .thenReturn(Optional.of("lock"));
        when(incrementalRagUpdateService.performIncrementalUpdate(
                any(), any(), anyString(), anyString(), anyString(), anyString(), anySet(), anySet(), anySet()))
                .thenReturn(Map.of("updatedFiles", 1, "deletedFiles", 0, "skippedFiles", 0));

        VcsClient mockVcs = mock(VcsClient.class);
        when(vcsClientProvider.getClient(any(VcsConnection.class))).thenReturn(mockVcs);
        when(mockVcs.getLatestCommitHash("my-workspace", "my-repo", "feature")).thenReturn("develop-401");
        when(mockVcs.getBranchDiff("my-workspace", "my-repo", "develop-400", "develop-401"))
                .thenReturn("checkpoint diff");
        RagBranchIndex checkpoint = new RagBranchIndex(testProject, "feature");
        checkpoint.setCommitHash("develop-400");
        when(ragBranchIndexRepository.findByProjectIdAndBranchName(100L, "feature"))
                .thenReturn(Optional.of(checkpoint));
        @SuppressWarnings("unchecked")
        Consumer<Map<String, Object>> eventConsumer = mock(Consumer.class);

        boolean result = service.updateBranchIndex(testProject, "feature", eventConsumer);

        assertThat(result).isTrue();
        verify(mockVcs).getBranchDiff("my-workspace", "my-repo", "develop-400", "develop-401");
        verify(mockVcs, never()).getBranchDiff("my-workspace", "my-repo", "main", "feature");
    }

    @Test
    void updateBranchIndexRejectsBranchOutsideRetainedConfiguration() {
        setupRagEnabled();
        when(ragIndexTrackingService.isProjectIndexed(testProject)).thenReturn(true);
        @SuppressWarnings("unchecked")
        Consumer<Map<String, Object>> eventConsumer = mock(Consumer.class);

        boolean result = service.updateBranchIndex(testProject, "release/preview", eventConsumer);

        assertThat(result).isFalse();
        verifyNoInteractions(vcsClientProvider);
        verify(eventConsumer).accept(argThat(event -> "rag_skipped".equals(event.get("state"))));
    }

    @Test
    void branchPushPatternDoesNotAuthorizeRetainedRagIndexUpdate() {
        ReflectionTestUtils.setField(service, "ragApiEnabled", true);
        RagConfig ragConfig = new RagConfig(
                true, "main", null, null, true, 30, null, false);
        ProjectConfig config = new ProjectConfig(
                false,
                "main",
                new BranchAnalysisConfig(List.of("main"), List.of("release/**")),
                ragConfig);
        testProject.setConfiguration(config);
        when(ragIndexTrackingService.isProjectIndexed(testProject)).thenReturn(true);
        @SuppressWarnings("unchecked")
        Consumer<Map<String, Object>> eventConsumer = mock(Consumer.class);

        boolean result = service.updateBranchIndex(
                testProject, "release/preview", eventConsumer);

        assertThat(result).isFalse();
        verifyNoInteractions(vcsClientProvider);
        verify(eventConsumer).accept(argThat(event ->
                "rag_skipped".equals(event.get("state"))
                        && event.get("message").toString().contains("not configured")));
    }

    @Test
    void updateBranchIndexEmptyDiffSeedsExactSnapshot() throws Exception {
        RagBranchIndexRegistryService registry = mock(RagBranchIndexRegistryService.class);
        BranchIndexGenerationBuildService builder = mock(BranchIndexGenerationBuildService.class);
        service = new RagOperationsServiceImpl(
                ragIndexTrackingService, incrementalRagUpdateService,
                analysisLockService, analysisJobService,
                ragBranchIndexRepository, vcsClientProvider,
                ragPipelineClient, registry, builder);
        setupRagEnabled();
        setupVcsBinding();
        service = spy(service);
        doReturn(true).when(service).shouldHaveBranchIndex(testProject, "feature");
        when(ragIndexTrackingService.isProjectIndexed(testProject)).thenReturn(true);
        VcsClient mockVcs = mock(VcsClient.class);
        when(vcsClientProvider.getClient(any(VcsConnection.class))).thenReturn(mockVcs);
        when(mockVcs.getLatestCommitHash("my-workspace", "my-repo", "feature"))
                .thenReturn("feature-head");
        doReturn(true).when(service).triggerIncrementalUpdate(
                eq(testProject), eq("feature"), eq("feature-head"), eq(""), any());
        @SuppressWarnings("unchecked")
        Consumer<Map<String, Object>> eventConsumer = mock(Consumer.class);

        boolean result = service.updateBranchIndex(testProject, "feature", eventConsumer);

        assertThat(result).isTrue();
        verify(service).triggerIncrementalUpdate(
                testProject, "feature", "feature-head", "", eventConsumer);
    }

    @Test
    void testUpdateBranchIndex_VcsClientException() throws Exception {
        setupRagEnabled();
        setupVcsBinding();
        when(ragIndexTrackingService.isProjectIndexed(testProject)).thenReturn(true);
        when(vcsClientProvider.getClient(any(VcsConnection.class))).thenThrow(new RuntimeException("VCS down"));
        @SuppressWarnings("unchecked")
        Consumer<Map<String, Object>> eventConsumer = mock(Consumer.class);

        boolean result = service.updateBranchIndex(testProject, "feature", eventConsumer);

        assertThat(result).isFalse();
        verify(eventConsumer).accept(argThat(m -> "error".equals(m.get("type"))));
    }

    // ── ensureBranchIndexForPrTarget full-flow tests ────────────────────

    @Test
    void testEnsureBranchIndexForPrTarget_SuccessWithDiff() throws Exception {
        setupRagEnabled();
        setupVcsBinding();
        when(ragIndexTrackingService.isProjectIndexed(testProject)).thenReturn(true);
        VcsClient mockVcs = mock(VcsClient.class);
        when(vcsClientProvider.getClient(any(VcsConnection.class))).thenReturn(mockVcs);
        when(mockVcs.getBranchDiff("my-workspace", "my-repo", "main", "feature")).thenReturn("diff data");
        when(mockVcs.getLatestCommitHash("my-workspace", "my-repo", "feature")).thenReturn("xyz789");
        stubSuccessfulIncrementalUpdate("feature", "xyz789");
        @SuppressWarnings("unchecked")
        Consumer<Map<String, Object>> eventConsumer = mock(Consumer.class);

        boolean result = service.ensureBranchIndexForPrTarget(testProject, "feature", eventConsumer);

        assertThat(result).isTrue();
    }

    @Test
    void testEnsureBranchIndexForPrTarget_EmptyDiff() throws Exception {
        setupRagEnabled();
        setupVcsBinding();
        when(ragIndexTrackingService.isProjectIndexed(testProject)).thenReturn(true);
        VcsClient mockVcs = mock(VcsClient.class);
        when(vcsClientProvider.getClient(any(VcsConnection.class))).thenReturn(mockVcs);
        when(mockVcs.getBranchDiff("my-workspace", "my-repo", "main", "feature")).thenReturn(null);
        @SuppressWarnings("unchecked")
        Consumer<Map<String, Object>> eventConsumer = mock(Consumer.class);

        boolean result = service.ensureBranchIndexForPrTarget(testProject, "feature", eventConsumer);

        assertThat(result).isTrue();
    }

    @Test
    void testEnsureBranchIndexForPrTarget_VcsException() throws Exception {
        setupRagEnabled();
        setupVcsBinding();
        when(ragIndexTrackingService.isProjectIndexed(testProject)).thenReturn(true);
        when(vcsClientProvider.getClient(any(VcsConnection.class)))
                .thenThrow(new RuntimeException("Connection refused"));
        @SuppressWarnings("unchecked")
        Consumer<Map<String, Object>> eventConsumer = mock(Consumer.class);

        boolean result = service.ensureBranchIndexForPrTarget(testProject, "feature", eventConsumer);

        assertThat(result).isFalse();
    }

    // ── ensureRagIndexUpToDate full-flow tests ──────────────────────────

    @Test
    void testEnsureRagIndexUpToDate_MainBranch_UpToDate() throws Exception {
        setupRagEnabled();
        setupVcsBinding();
        when(ragIndexTrackingService.isProjectIndexed(testProject)).thenReturn(true);
        VcsClient mockVcs = mock(VcsClient.class);
        when(vcsClientProvider.getClient(any(VcsConnection.class))).thenReturn(mockVcs);
        when(mockVcs.getLatestCommitHash("my-workspace", "my-repo", "main")).thenReturn("same-commit");
        RagIndexStatus status = mock(RagIndexStatus.class);
        when(status.getIndexedCommitHash()).thenReturn("same-commit");
        when(ragIndexTrackingService.getIndexStatus(testProject)).thenReturn(Optional.of(status));
        @SuppressWarnings("unchecked")
        Consumer<Map<String, Object>> eventConsumer = mock(Consumer.class);

        boolean result = service.ensureRagIndexUpToDate(testProject, "main", eventConsumer);

        assertThat(result).isTrue();
        verify(mockVcs, never()).getBranchDiff(anyString(), anyString(), anyString(), anyString());
    }

    @Test
    void testEnsureRagIndexUpToDate_MainBranch_Outdated() throws Exception {
        service = spy(service);
        setupRagEnabled();
        setupVcsBinding();
        when(ragIndexTrackingService.isProjectIndexed(testProject)).thenReturn(true);
        VcsClient mockVcs = mock(VcsClient.class);
        when(vcsClientProvider.getClient(any(VcsConnection.class))).thenReturn(mockVcs);
        when(mockVcs.getLatestCommitHash("my-workspace", "my-repo", "main")).thenReturn("new-commit");
        RagIndexStatus status = mock(RagIndexStatus.class);
        when(status.getIndexedCommitHash()).thenReturn("old-commit");
        when(ragIndexTrackingService.getIndexStatus(testProject)).thenReturn(Optional.of(status));
        @SuppressWarnings("unchecked")
        Consumer<Map<String, Object>> eventConsumer = mock(Consumer.class);
        doReturn(true).when(service).triggerIncrementalUpdate(
                testProject, "main", "new-commit", "", eventConsumer);

        boolean result = service.ensureRagIndexUpToDate(testProject, "main", eventConsumer);

        assertThat(result).isTrue();
        verify(service).triggerIncrementalUpdate(
                testProject, "main", "new-commit", "", eventConsumer);
        verify(mockVcs, never()).getBranchDiff(anyString(), anyString(), anyString(), anyString());
    }

    @Test
    void outdatedMainCheckpointCannotBeAdvancedOutsideDurableTrigger() throws Exception {
        service = spy(service);
        setupRagEnabled();
        setupVcsBinding();
        when(ragIndexTrackingService.isProjectIndexed(testProject)).thenReturn(true);
        VcsClient mockVcs = mock(VcsClient.class);
        when(vcsClientProvider.getClient(any(VcsConnection.class))).thenReturn(mockVcs);
        when(mockVcs.getLatestCommitHash("my-workspace", "my-repo", "main")).thenReturn("new-commit");
        RagIndexStatus status = mock(RagIndexStatus.class);
        when(status.getIndexedCommitHash()).thenReturn("old-commit");
        when(ragIndexTrackingService.getIndexStatus(testProject)).thenReturn(Optional.of(status));
        @SuppressWarnings("unchecked")
        Consumer<Map<String, Object>> eventConsumer = mock(Consumer.class);
        doReturn(false).when(service).triggerIncrementalUpdate(
                testProject, "main", "new-commit", "", eventConsumer);

        boolean result = service.ensureRagIndexUpToDate(testProject, "main", eventConsumer);

        assertThat(result).isFalse();
        verify(service).triggerIncrementalUpdate(
                testProject, "main", "new-commit", "", eventConsumer);
        verify(ragIndexTrackingService, never()).markUpdatingCompleted(
                any(), anyString(), anyString(), any(), any(), any());
        verify(mockVcs, never()).getBranchDiff(anyString(), anyString(), anyString(), anyString());
    }

    @Test
    void testEnsureRagIndexUpToDate_MainBranch_NoIndexStatus() throws Exception {
        setupRagEnabled();
        setupVcsBinding();
        when(ragIndexTrackingService.isProjectIndexed(testProject)).thenReturn(true);
        VcsClient mockVcs = mock(VcsClient.class);
        when(vcsClientProvider.getClient(any(VcsConnection.class))).thenReturn(mockVcs);
        when(mockVcs.getLatestCommitHash("my-workspace", "my-repo", "main")).thenReturn("commit");
        when(ragIndexTrackingService.getIndexStatus(testProject)).thenReturn(Optional.empty());
        @SuppressWarnings("unchecked")
        Consumer<Map<String, Object>> eventConsumer = mock(Consumer.class);

        boolean result = service.ensureRagIndexUpToDate(testProject, "main", eventConsumer);

        assertThat(result).isFalse();
    }

    @Test
    void testEnsureRagIndexUpToDate_DifferentBranch_NoBranchIndex() throws Exception {
        setupRagEnabled();
        setupVcsBinding();
        when(ragIndexTrackingService.isProjectIndexed(testProject)).thenReturn(true);
        VcsClient mockVcs = mock(VcsClient.class);
        when(vcsClientProvider.getClient(any(VcsConnection.class))).thenReturn(mockVcs);
        // ensureMainIndexUpToDate
        when(mockVcs.getLatestCommitHash("my-workspace", "my-repo", "main")).thenReturn("main-commit");
        RagIndexStatus status = mock(RagIndexStatus.class);
        when(status.getIndexedCommitHash()).thenReturn("main-commit");
        when(ragIndexTrackingService.getIndexStatus(testProject)).thenReturn(Optional.of(status));
        // ensureBranchIndexUpToDate - no branch index exists
        when(mockVcs.getLatestCommitHash("my-workspace", "my-repo", "feature")).thenReturn("feat-commit");
        when(ragBranchIndexRepository.findByProjectIdAndBranchName(100L, "feature"))
                .thenReturn(Optional.empty());
        // ensureBranchIndexForPrTarget called
        when(ragBranchIndexRepository.existsByProjectIdAndBranchName(100L, "feature")).thenReturn(false);
        when(mockVcs.getBranchDiff("my-workspace", "my-repo", "main", "feature")).thenReturn("branch diff");
        stubSuccessfulIncrementalUpdate("feature", "feat-commit");
        @SuppressWarnings("unchecked")
        Consumer<Map<String, Object>> eventConsumer = mock(Consumer.class);

        boolean result = service.ensureRagIndexUpToDate(testProject, "feature", eventConsumer);

        assertThat(result).isTrue();
    }

    private void stubSuccessfulIncrementalUpdate(String branchName, String commitHash)
            throws Exception {
        Job mockJob = mock(Job.class);
        when(mockJob.getId()).thenReturn(81L);
        when(incrementalRagUpdateService.shouldPerformIncrementalUpdate(testProject))
                .thenReturn(true);
        when(incrementalRagUpdateService.parseDiffForRag(anyString()))
                .thenReturn(new IncrementalRagUpdateService.DiffResult(
                        Set.of("src/A.java"), Set.of(), Set.of()));
        when(analysisJobService.createRagIndexJob(
                any(), anyBoolean(), any(), anyString(), anyString()))
                .thenReturn(mockJob);
        when(analysisLockService.acquireLock(
                any(), eq(branchName), any(), eq(commitHash), isNull()))
                .thenReturn(Optional.of("lock-key-" + branchName));
        when(incrementalRagUpdateService.performIncrementalUpdate(
                any(), any(), anyString(), anyString(), anyString(), anyString(), any(),
                any(), any()))
                .thenReturn(Map.of("updatedFiles", 1, "deletedFiles", 0));
    }

    @Test
    void testEnsureRagIndexUpToDate_DifferentBranch_UpToDate() throws Exception {
        setupRagEnabled();
        setupVcsBinding();
        when(ragIndexTrackingService.isProjectIndexed(testProject)).thenReturn(true);
        VcsClient mockVcs = mock(VcsClient.class);
        when(vcsClientProvider.getClient(any(VcsConnection.class))).thenReturn(mockVcs);
        // ensureMainIndexUpToDate
        when(mockVcs.getLatestCommitHash("my-workspace", "my-repo", "main")).thenReturn("m1");
        RagIndexStatus status = mock(RagIndexStatus.class);
        when(status.getIndexedCommitHash()).thenReturn("m1");
        when(ragIndexTrackingService.getIndexStatus(testProject)).thenReturn(Optional.of(status));
        // ensureBranchIndexUpToDate - branch index exists and up-to-date
        when(mockVcs.getLatestCommitHash("my-workspace", "my-repo", "feature")).thenReturn("f1");
        RagBranchIndex branchIndex = new RagBranchIndex();
        branchIndex.setCommitHash("f1");
        when(ragBranchIndexRepository.findByProjectIdAndBranchName(100L, "feature"))
                .thenReturn(Optional.of(branchIndex));
        @SuppressWarnings("unchecked")
        Consumer<Map<String, Object>> eventConsumer = mock(Consumer.class);

        boolean result = service.ensureRagIndexUpToDate(testProject, "feature", eventConsumer);

        assertThat(result).isTrue();
    }

    @Test
    void testEnsureRagIndexUpToDate_DifferentBranch_Outdated() throws Exception {
        service = spy(service);
        setupRagEnabled();
        setupVcsBinding();
        when(ragIndexTrackingService.isProjectIndexed(testProject)).thenReturn(true);
        VcsClient mockVcs = mock(VcsClient.class);
        when(vcsClientProvider.getClient(any(VcsConnection.class))).thenReturn(mockVcs);
        // ensureMainIndexUpToDate
        when(mockVcs.getLatestCommitHash("my-workspace", "my-repo", "main")).thenReturn("m1");
        RagIndexStatus status = mock(RagIndexStatus.class);
        when(status.getIndexedCommitHash()).thenReturn("m1");
        when(ragIndexTrackingService.getIndexStatus(testProject)).thenReturn(Optional.of(status));
        // ensureBranchIndexUpToDate - branch exists but outdated
        when(mockVcs.getLatestCommitHash("my-workspace", "my-repo", "feature")).thenReturn("f2");
        RagBranchIndex branchIndex = new RagBranchIndex();
        branchIndex.setCommitHash("f1");
        when(ragBranchIndexRepository.findByProjectIdAndBranchName(100L, "feature"))
                .thenReturn(Optional.of(branchIndex));
        @SuppressWarnings("unchecked")
        Consumer<Map<String, Object>> eventConsumer = mock(Consumer.class);
        doReturn(false).when(service).triggerIncrementalUpdate(
                testProject, "feature", "f2", "", eventConsumer);

        boolean result = service.ensureRagIndexUpToDate(testProject, "feature", eventConsumer);

        assertThat(result).isFalse();
        verify(service).triggerIncrementalUpdate(
                testProject, "feature", "f2", "", eventConsumer);
        verify(mockVcs, never()).getBranchDiff(anyString(), anyString(), anyString(), anyString());
    }

    @Test
    void outdatedBranchCheckpointCannotBeSavedOutsideDurableTrigger() throws Exception {
        service = spy(service);
        setupRagEnabled();
        setupVcsBinding();
        when(ragIndexTrackingService.isProjectIndexed(testProject)).thenReturn(true);
        VcsClient mockVcs = mock(VcsClient.class);
        when(vcsClientProvider.getClient(any(VcsConnection.class))).thenReturn(mockVcs);
        // ensureMainIndexUpToDate
        when(mockVcs.getLatestCommitHash("my-workspace", "my-repo", "main")).thenReturn("m1");
        RagIndexStatus status = mock(RagIndexStatus.class);
        when(status.getIndexedCommitHash()).thenReturn("m1");
        when(ragIndexTrackingService.getIndexStatus(testProject)).thenReturn(Optional.of(status));
        // ensureBranchIndexUpToDate - branch outdated but null diff
        when(mockVcs.getLatestCommitHash("my-workspace", "my-repo", "feature")).thenReturn("f2");
        RagBranchIndex branchIndex = new RagBranchIndex();
        branchIndex.setCommitHash("f1");
        when(ragBranchIndexRepository.findByProjectIdAndBranchName(100L, "feature"))
                .thenReturn(Optional.of(branchIndex));
        @SuppressWarnings("unchecked")
        Consumer<Map<String, Object>> eventConsumer = mock(Consumer.class);
        doReturn(true).when(service).triggerIncrementalUpdate(
                testProject, "feature", "f2", "", eventConsumer);

        boolean result = service.ensureRagIndexUpToDate(testProject, "feature", eventConsumer);

        assertThat(result).isTrue();
        verify(service).triggerIncrementalUpdate(
                testProject, "feature", "f2", "", eventConsumer);
        verify(ragBranchIndexRepository, never()).save(branchIndex);
        verify(mockVcs, never()).getBranchDiff(anyString(), anyString(), anyString(), anyString());
    }

    @Test
    void testEnsureRagIndexUpToDate_Exception() throws Exception {
        setupRagEnabled();
        setupVcsBinding();
        when(ragIndexTrackingService.isProjectIndexed(testProject)).thenReturn(true);
        when(vcsClientProvider.getClient(any(VcsConnection.class)))
                .thenThrow(new RuntimeException("VCS error"));
        @SuppressWarnings("unchecked")
        Consumer<Map<String, Object>> eventConsumer = mock(Consumer.class);

        boolean result = service.ensureRagIndexUpToDate(testProject, "main", eventConsumer);

        // Falls back to isRagIndexReady
        assertThat(result).isTrue();
    }

    // ── cleanupStaleBranches additional tests ───────────────────────────

    @Test
    void testCleanupStaleBranches_PipelineException() {
        setupRagEnabled();
        setupVcsBinding();
        when(ragPipelineClient.getIndexedBranches("my-workspace", "my-repo"))
                .thenThrow(new RuntimeException("Pipeline down"));
        @SuppressWarnings("unchecked")
        Consumer<Map<String, Object>> eventConsumer = mock(Consumer.class);

        Map<String, Object> result = service.cleanupStaleBranches(testProject, Set.of("feature"), eventConsumer);

        assertThat(result).containsEntry("status", "error");
    }

    @Test
    void testCleanupStaleBranches_PartialFailure() throws Exception {
        setupRagEnabled();
        setupVcsBinding();
        when(ragPipelineClient.getIndexedBranches("my-workspace", "my-repo"))
                .thenReturn(List.of("main", "stale1", "stale2"));
        when(ragPipelineClient.deleteBranchWithOutcome(
                "my-workspace", "my-repo", "stale1", null))
                .thenReturn(RagPipelineClient.BranchDeletionOutcome.success("legacy-alias"));
        when(ragPipelineClient.deleteBranchWithOutcome(
                "my-workspace", "my-repo", "stale2", null))
                .thenReturn(RagPipelineClient.BranchDeletionOutcome.failure(
                        "legacy-alias", RagPipelineClient.BranchDeletionFailure.TARGET,
                        404, "not found"));
        @SuppressWarnings("unchecked")
        Consumer<Map<String, Object>> eventConsumer = mock(Consumer.class);

        Map<String, Object> result = service.cleanupStaleBranches(testProject, java.util.Collections.<String>emptySet(),
                eventConsumer);

        assertThat(result).containsEntry("status", "success");
        assertThat(result).containsEntry("total_deleted", 1);
        @SuppressWarnings("unchecked")
        List<String> failed = (List<String>) result.get("failed_branches");
        assertThat(failed).contains("stale2");
    }

    @Test
    void testCleanupStaleBranches_DeleteThrows() throws Exception {
        setupRagEnabled();
        setupVcsBinding();
        when(ragPipelineClient.getIndexedBranches("my-workspace", "my-repo"))
                .thenReturn(List.of("main", "stale1"));
        when(ragPipelineClient.deleteBranchWithOutcome(
                "my-workspace", "my-repo", "stale1", null))
                .thenThrow(new RuntimeException("Connection error"));
        @SuppressWarnings("unchecked")
        Consumer<Map<String, Object>> eventConsumer = mock(Consumer.class);

        Map<String, Object> result = service.cleanupStaleBranches(testProject, java.util.Collections.<String>emptySet(),
                eventConsumer);

        assertThat(result).containsEntry("status", "success");
        @SuppressWarnings("unchecked")
        List<String> failed = (List<String>) result.get("failed_branches");
        assertThat(failed).contains("stale1");
    }

    // ── Helper methods ──────────────────────────────────────────────────────

    private void setupRagEnabled() {
        ReflectionTestUtils.setField(service, "ragApiEnabled", true);
        RagConfig ragConfig = new RagConfig(
                true, "main", null, null, true, 30, List.of("feature"), true);
        ProjectConfig config = new ProjectConfig(false, "main", null, ragConfig);
        testProject.setConfiguration(config);
    }

    private void setupVcsBinding() {
        VcsRepoBinding binding = new VcsRepoBinding();
        VcsConnection connection = new VcsConnection();
        binding.setVcsConnection(connection);
        binding.setExternalNamespace("my-workspace");
        binding.setExternalRepoSlug("my-repo");
        testProject.setVcsRepoBinding(binding);
    }

    private void setupProjectWithWorkspaceAndNamespace() {
        Workspace workspace = new Workspace();
        ReflectionTestUtils.setField(workspace, "name", "test-ws");
        testProject.setWorkspace(workspace);
        testProject.setNamespace("test-ns");
    }

    // ── deletePrFiles tests ─────────────────────────────────────────────────

    @Test
    void testDeletePrFiles_Success() {
        ReflectionTestUtils.setField(service, "ragApiEnabled", true);
        setupProjectWithWorkspaceAndNamespace();
        when(ragPipelineClient.deletePrFilesWithOutcome("test-ws", "test-ns", 42, null))
                .thenReturn(RagPipelineClient.PrFilesDeletionOutcome.success("legacy-alias"));

        boolean result = service.deletePrFiles(testProject, 42);

        assertThat(result).isTrue();
        verify(ragPipelineClient).deletePrFilesWithOutcome("test-ws", "test-ns", 42, null);
    }

    @Test
    void deletePrFilesCleansEveryPublishedGenerationAndDeduplicatesTargets() {
        ReflectionTestUtils.setField(service, "ragApiEnabled", true);
        setupProjectWithWorkspaceAndNamespace();
        when(branchGenerationRepository.findCollectionNamesByProjectIdAndStatusIn(
                eq(100L),
                eq(List.of(
                        org.rostilos.codecrow.core.model.rag.RagBranchIndexGenerationStatus.ACTIVE,
                        org.rostilos.codecrow.core.model.rag.RagBranchIndexGenerationStatus.SUPERSEDED))))
                .thenReturn(List.of("generation-a", "generation-a", "  ", "generation-b"));
        when(ragPipelineClient.deletePrFilesWithOutcome(
                "test-ws", "test-ns", 42, "generation-a"))
                .thenReturn(RagPipelineClient.PrFilesDeletionOutcome.success("generation-a"));
        when(ragPipelineClient.deletePrFilesWithOutcome(
                "test-ws", "test-ns", 42, "generation-b"))
                .thenReturn(RagPipelineClient.PrFilesDeletionOutcome.success("generation-b"));

        boolean result = service.deletePrFiles(testProject, 42);

        assertThat(result).isTrue();
        verify(ragPipelineClient).deletePrFilesWithOutcome(
                "test-ws", "test-ns", 42, "generation-a");
        verify(ragPipelineClient).deletePrFilesWithOutcome(
                "test-ws", "test-ns", 42, "generation-b");
        verify(ragPipelineClient, never()).deletePrFilesWithOutcome(
                "test-ws", "test-ns", 42, null);
    }

    @Test
    void deletePrFilesContinuesAfterOneTargetSpecificRejection() {
        ReflectionTestUtils.setField(service, "ragApiEnabled", true);
        setupProjectWithWorkspaceAndNamespace();
        when(branchGenerationRepository.findCollectionNamesByProjectIdAndStatusIn(
                eq(100L), anyList()))
                .thenReturn(List.of("generation-a", "generation-b"));
        when(ragPipelineClient.deletePrFilesWithOutcome(
                "test-ws", "test-ns", 42, "generation-a"))
                .thenReturn(RagPipelineClient.PrFilesDeletionOutcome.failure(
                        "generation-a",
                        RagPipelineClient.PrFilesDeletionFailure.TARGET,
                        404,
                        "collection missing"));
        when(ragPipelineClient.deletePrFilesWithOutcome(
                "test-ws", "test-ns", 42, "generation-b"))
                .thenReturn(RagPipelineClient.PrFilesDeletionOutcome.success("generation-b"));

        boolean result = service.deletePrFiles(testProject, 42);

        assertThat(result).isFalse();
        verify(ragPipelineClient).deletePrFilesWithOutcome(
                "test-ws", "test-ns", 42, "generation-a");
        verify(ragPipelineClient).deletePrFilesWithOutcome(
                "test-ws", "test-ns", 42, "generation-b");
    }

    @Test
    void deletePrFilesStopsAfterServiceFailureAndNamesFailingTarget() {
        ReflectionTestUtils.setField(service, "ragApiEnabled", true);
        setupProjectWithWorkspaceAndNamespace();
        when(branchGenerationRepository.findCollectionNamesByProjectIdAndStatusIn(
                eq(100L), anyList()))
                .thenReturn(List.of("generation-a", "generation-b"));
        when(ragPipelineClient.deletePrFilesWithOutcome(
                "test-ws", "test-ns", 42, "generation-a"))
                .thenReturn(RagPipelineClient.PrFilesDeletionOutcome.failure(
                        "generation-a",
                        RagPipelineClient.PrFilesDeletionFailure.SERVICE,
                        409,
                        "mutation lease unavailable"));

        Logger logger = (Logger) LoggerFactory.getLogger(RagOperationsServiceImpl.class);
        ListAppender<ILoggingEvent> appender = new ListAppender<>();
        appender.start();
        logger.addAppender(appender);
        boolean result;
        try {
            result = service.deletePrFiles(testProject, 42);
        } finally {
            logger.detachAppender(appender);
            appender.stop();
        }

        assertThat(result).isFalse();
        verify(ragPipelineClient).deletePrFilesWithOutcome(
                "test-ws", "test-ns", 42, "generation-a");
        verify(ragPipelineClient, never()).deletePrFilesWithOutcome(
                "test-ws", "test-ns", 42, "generation-b");
        assertThat(appender.list.stream()
                .filter(event -> event.getLevel() == Level.WARN)
                .map(ILoggingEvent::getFormattedMessage))
                .containsExactly("Failed to delete PR #42 files for project=100 "
                        + "target=generation-a: status=409 detail=mutation lease unavailable");
    }

    @Test
    void testDeletePrFiles_PipelineReturnsFalse() {
        ReflectionTestUtils.setField(service, "ragApiEnabled", true);
        setupProjectWithWorkspaceAndNamespace();
        when(ragPipelineClient.deletePrFilesWithOutcome("test-ws", "test-ns", 42, null))
                .thenReturn(RagPipelineClient.PrFilesDeletionOutcome.failure(
                        "legacy-alias",
                        RagPipelineClient.PrFilesDeletionFailure.TARGET,
                        404,
                        "not found"));

        boolean result = service.deletePrFiles(testProject, 42);

        assertThat(result).isFalse();
    }

    @Test
    void testDeletePrFiles_PipelineThrowsException() {
        ReflectionTestUtils.setField(service, "ragApiEnabled", true);
        setupProjectWithWorkspaceAndNamespace();
        when(ragPipelineClient.deletePrFilesWithOutcome("test-ws", "test-ns", 42, null))
                .thenThrow(new RuntimeException("Connection timeout"));

        boolean result = service.deletePrFiles(testProject, 42);

        assertThat(result).isFalse();
    }

    @Test
    void testDeletePrFiles_WhenDisabled_ReturnsTrue() {
        ReflectionTestUtils.setField(service, "ragApiEnabled", false);

        boolean result = service.deletePrFiles(testProject, 42);

        assertThat(result).isTrue();
        verifyNoInteractions(ragPipelineClient);
    }
}
