package org.rostilos.codecrow.ragengine.service;

import org.junit.jupiter.api.BeforeEach;
import org.junit.jupiter.api.Test;
import org.junit.jupiter.api.extension.ExtendWith;
import org.mockito.Mock;
import org.mockito.junit.jupiter.MockitoExtension;
import org.rostilos.codecrow.analysisengine.service.AnalysisLockService;
import org.rostilos.codecrow.core.model.project.Project;
import org.rostilos.codecrow.core.persistence.repository.rag.RagDeltaIndexRepository;
import org.rostilos.codecrow.core.service.AnalysisJobService;
import org.rostilos.codecrow.vcsclient.VcsClientProvider;
import org.springframework.test.util.ReflectionTestUtils;

import java.util.Map;
import java.util.function.Consumer;

import static org.assertj.core.api.Assertions.assertThat;
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
    private RagDeltaIndexRepository ragDeltaIndexRepository;

    @Mock
    private DeltaIndexService deltaIndexService;

    @Mock
    private VcsClientProvider vcsClientProvider;

    private RagOperationsServiceImpl service;
    private Project testProject;

    @BeforeEach
    void setUp() {
        service = new RagOperationsServiceImpl(
                ragIndexTrackingService,
                incrementalRagUpdateService,
                analysisLockService,
                analysisJobService,
                ragDeltaIndexRepository,
                deltaIndexService,
                vcsClientProvider
        );
        
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
    void testIsRagEnabled_ShouldNotPerformUpdate() {
        ReflectionTestUtils.setField(service, "ragApiEnabled", true);
        when(incrementalRagUpdateService.shouldPerformIncrementalUpdate(testProject)).thenReturn(false);

        boolean result = service.isRagEnabled(testProject);

        assertThat(result).isFalse();
    }

    @Test
    void testIsRagEnabled_Success() {
        ReflectionTestUtils.setField(service, "ragApiEnabled", true);
        when(incrementalRagUpdateService.shouldPerformIncrementalUpdate(testProject)).thenReturn(true);

        boolean result = service.isRagEnabled(testProject);

        assertThat(result).isTrue();
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
        when(incrementalRagUpdateService.shouldPerformIncrementalUpdate(testProject)).thenReturn(true);
        when(ragIndexTrackingService.isProjectIndexed(testProject)).thenReturn(false);

        boolean result = service.isRagIndexReady(testProject);

        assertThat(result).isFalse();
    }

    @Test
    void testIsRagIndexReady_Success() {
        ReflectionTestUtils.setField(service, "ragApiEnabled", true);
        when(incrementalRagUpdateService.shouldPerformIncrementalUpdate(testProject)).thenReturn(true);
        when(ragIndexTrackingService.isProjectIndexed(testProject)).thenReturn(true);

        boolean result = service.isRagIndexReady(testProject);

        assertThat(result).isTrue();
    }

    @Test
    void testIsDeltaIndexReady_True() {
        when(ragDeltaIndexRepository.existsReadyDeltaIndex(100L, "feature")).thenReturn(true);

        boolean result = service.isDeltaIndexReady(testProject, "feature");

        assertThat(result).isTrue();
        verify(ragDeltaIndexRepository).existsReadyDeltaIndex(100L, "feature");
    }

    @Test
    void testIsDeltaIndexReady_False() {
        when(ragDeltaIndexRepository.existsReadyDeltaIndex(100L, "feature")).thenReturn(false);

        boolean result = service.isDeltaIndexReady(testProject, "feature");

        assertThat(result).isFalse();
    }

    @Test
    void testTriggerIncrementalUpdate_WhenNotEnabled() {
        ReflectionTestUtils.setField(service, "ragApiEnabled", false);
        @SuppressWarnings("unchecked")
        Consumer<Map<String, Object>> eventConsumer = mock(Consumer.class);

        service.triggerIncrementalUpdate(testProject, "main", "abc123", "diff", eventConsumer);

        verifyNoInteractions(analysisJobService);
        verifyNoInteractions(analysisLockService);
    }

    @Test
    void testCreateOrUpdateDeltaIndex_WhenDeltaIndexDisabled() {
        ReflectionTestUtils.setField(service, "ragApiEnabled", false);
        @SuppressWarnings("unchecked")
        Consumer<Map<String, Object>> eventConsumer = mock(Consumer.class);

        service.createOrUpdateDeltaIndex(testProject, "feature", "main", "commit123", "diff", eventConsumer);

        verifyNoInteractions(analysisJobService);
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
    void testEnsureDeltaIndexForPrTarget_WhenNotEnabled() {
        ReflectionTestUtils.setField(service, "ragApiEnabled", false);
        @SuppressWarnings("unchecked")
        Consumer<Map<String, Object>> eventConsumer = mock(Consumer.class);

        boolean result = service.ensureDeltaIndexForPrTarget(testProject, "feature", eventConsumer);

        assertThat(result).isFalse();
    }

    @Test
    void testConstructor() {
        assertThat(service).isNotNull();
    }
}
