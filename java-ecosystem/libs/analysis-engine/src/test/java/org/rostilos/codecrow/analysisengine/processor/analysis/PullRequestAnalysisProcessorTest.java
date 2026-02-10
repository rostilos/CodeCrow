package org.rostilos.codecrow.analysisengine.processor.analysis;

import org.junit.jupiter.api.BeforeEach;
import org.junit.jupiter.api.DisplayName;
import org.junit.jupiter.api.Nested;
import org.junit.jupiter.api.Test;
import org.junit.jupiter.api.extension.ExtendWith;
import org.mockito.ArgumentCaptor;
import org.mockito.Mock;
import org.mockito.junit.jupiter.MockitoExtension;
import org.rostilos.codecrow.analysisengine.aiclient.AiAnalysisClient;
import org.rostilos.codecrow.analysisengine.dto.request.ai.AiAnalysisRequest;
import org.rostilos.codecrow.analysisengine.dto.request.processor.PrProcessRequest;
import org.rostilos.codecrow.analysisengine.exception.AnalysisLockedException;
import org.rostilos.codecrow.analysisengine.service.AnalysisLockService;
import org.rostilos.codecrow.analysisengine.service.PullRequestService;
import org.rostilos.codecrow.analysisengine.service.rag.RagOperationsService;
import org.rostilos.codecrow.analysisengine.service.vcs.VcsAiClientService;
import org.rostilos.codecrow.analysisengine.service.vcs.VcsReportingService;
import org.rostilos.codecrow.analysisengine.service.vcs.VcsServiceFactory;
import org.rostilos.codecrow.core.model.analysis.AnalysisLockType;
import org.rostilos.codecrow.core.model.codeanalysis.CodeAnalysis;
import org.rostilos.codecrow.core.model.codeanalysis.CodeAnalysisIssue;
import org.rostilos.codecrow.core.model.project.Project;
import org.rostilos.codecrow.core.model.pullrequest.PullRequest;
import org.rostilos.codecrow.core.model.vcs.EVcsProvider;
import org.rostilos.codecrow.core.model.vcs.VcsConnection;
import org.rostilos.codecrow.core.service.CodeAnalysisService;
import org.rostilos.codecrow.events.analysis.AnalysisCompletedEvent;
import org.rostilos.codecrow.events.analysis.AnalysisStartedEvent;
import org.springframework.context.ApplicationEventPublisher;

import java.io.IOException;
import java.security.GeneralSecurityException;
import java.util.*;
import java.util.function.Consumer;

import static org.assertj.core.api.Assertions.assertThat;
import static org.assertj.core.api.Assertions.assertThatThrownBy;
import static org.mockito.ArgumentMatchers.*;
import static org.mockito.Mockito.*;

@ExtendWith(MockitoExtension.class)
@DisplayName("PullRequestAnalysisProcessor")
class PullRequestAnalysisProcessorTest {

    @Mock
    private PullRequestService pullRequestService;

    @Mock
    private CodeAnalysisService codeAnalysisService;

    @Mock
    private AiAnalysisClient aiAnalysisClient;

    @Mock
    private VcsServiceFactory vcsServiceFactory;

    @Mock
    private AnalysisLockService analysisLockService;

    @Mock
    private RagOperationsService ragOperationsService;

    @Mock
    private ApplicationEventPublisher eventPublisher;

    @Mock
    private VcsReportingService reportingService;

    @Mock
    private VcsAiClientService aiClientService;

    @Mock
    private Project project;

    @Mock
    private VcsConnection vcsConnection;

    @Mock
    private PullRequest pullRequest;

    @Mock
    private CodeAnalysis codeAnalysis;

    @Mock
    private AiAnalysisRequest aiAnalysisRequest;

    private PullRequestAnalysisProcessor processor;

    @BeforeEach
    void setUp() {
        processor = new PullRequestAnalysisProcessor(
                pullRequestService,
                codeAnalysisService,
                aiAnalysisClient,
                vcsServiceFactory,
                analysisLockService,
                ragOperationsService,
                eventPublisher
        );
    }

    private PrProcessRequest createRequest() {
        PrProcessRequest request = new PrProcessRequest();
        request.projectId = 1L;
        request.pullRequestId = 42L;
        request.commitHash = "abc123";
        request.sourceBranchName = "feature-branch";
        request.targetBranchName = "main";
        return request;
    }

    @Nested
    @DisplayName("process()")
    class ProcessTests {

        @Test
        @DisplayName("should successfully process PR analysis")
        void shouldSuccessfullyProcessPRAnalysis() throws Exception {
            PrProcessRequest request = createRequest();
            PullRequestAnalysisProcessor.EventConsumer consumer = mock(PullRequestAnalysisProcessor.EventConsumer.class);

            // Setup mocks
            when(project.getEffectiveVcsConnection()).thenReturn(vcsConnection);
            when(project.getId()).thenReturn(1L);
            when(project.getName()).thenReturn("Test Project");
            when(vcsConnection.getProviderType()).thenReturn(EVcsProvider.BITBUCKET_CLOUD);

            when(analysisLockService.acquireLockWithWait(
                    any(), anyString(), any(), anyString(), anyLong(), any()
            )).thenReturn(Optional.of("lock-key-123"));

            when(pullRequestService.createOrUpdatePullRequest(
                    anyLong(), anyLong(), anyString(), anyString(), anyString(), any()
            )).thenReturn(pullRequest);

            when(vcsServiceFactory.getReportingService(EVcsProvider.BITBUCKET_CLOUD)).thenReturn(reportingService);
            when(vcsServiceFactory.getAiClientService(EVcsProvider.BITBUCKET_CLOUD)).thenReturn(aiClientService);

            when(codeAnalysisService.getCodeAnalysisCache(anyLong(), anyString(), anyLong()))
                    .thenReturn(Optional.empty());
            when(codeAnalysisService.getAnalysisByCommitHash(anyLong(), anyString()))
                    .thenReturn(Optional.empty());
            when(codeAnalysisService.getAllPrAnalyses(anyLong(), anyLong()))
                    .thenReturn(List.of());

            when(aiClientService.buildAiAnalysisRequest(any(), any(), any(), anyList())).thenReturn(aiAnalysisRequest);
            when(aiAnalysisRequest.getRawDiff()).thenReturn("");

            Map<String, Object> aiResponse = Map.of(
                    "comment", "Review comment",
                    "issues", List.of()
            );
            when(aiAnalysisClient.performAnalysis(any(), any())).thenReturn(aiResponse);

            when(codeAnalysisService.createAnalysisFromAiResponse(
                    any(), any(), anyLong(), anyString(), anyString(), anyString(), any(), any(), any()))
                    .thenReturn(codeAnalysis);

            Map<String, Object> result = processor.process(request, consumer, project);

            assertThat(result).containsKey("comment");
            verify(analysisLockService).acquireLockWithWait(any(), anyString(), any(), anyString(), anyLong(), any());
            verify(analysisLockService).releaseLock("lock-key-123");
            verify(reportingService).postAnalysisResults(any(), any(), anyLong(), any(), any());
        }

        @Test
        @DisplayName("should throw AnalysisLockedException when lock cannot be acquired")
        void shouldThrowAnalysisLockedExceptionWhenLockCannotBeAcquired() {
            PrProcessRequest request = createRequest();
            PullRequestAnalysisProcessor.EventConsumer consumer = mock(PullRequestAnalysisProcessor.EventConsumer.class);

            when(project.getId()).thenReturn(1L);
            when(project.getName()).thenReturn("Test Project");
            when(analysisLockService.acquireLockWithWait(any(), anyString(), any(), anyString(), anyLong(), any()))
                    .thenReturn(Optional.empty());
            when(analysisLockService.getLockWaitTimeoutMinutes()).thenReturn(10);

            assertThatThrownBy(() -> processor.process(request, consumer, project))
                    .isInstanceOf(AnalysisLockedException.class);
        }

        @Test
        @DisplayName("should return cached result when analysis cache exists")
        void shouldReturnCachedResultWhenAnalysisCacheExists() throws Exception {
            PrProcessRequest request = createRequest();
            PullRequestAnalysisProcessor.EventConsumer consumer = mock(PullRequestAnalysisProcessor.EventConsumer.class);

            when(project.getEffectiveVcsConnection()).thenReturn(vcsConnection);
            when(project.getId()).thenReturn(1L);
            when(project.getName()).thenReturn("Test Project");
            when(vcsConnection.getProviderType()).thenReturn(EVcsProvider.BITBUCKET_CLOUD);

            when(analysisLockService.acquireLockWithWait(any(), anyString(), any(), anyString(), anyLong(), any()))
                    .thenReturn(Optional.of("lock-key-123"));

            when(pullRequestService.createOrUpdatePullRequest(anyLong(), anyLong(), anyString(), anyString(), anyString(), any()))
                    .thenReturn(pullRequest);
            when(pullRequest.getId()).thenReturn(100L);

            when(vcsServiceFactory.getReportingService(EVcsProvider.BITBUCKET_CLOUD)).thenReturn(reportingService);
            when(codeAnalysisService.getCodeAnalysisCache(anyLong(), anyString(), anyLong()))
                    .thenReturn(Optional.of(codeAnalysis));

            Map<String, Object> result = processor.process(request, consumer, project);

            assertThat(result).containsEntry("status", "cached");
            assertThat(result).containsEntry("cached", true);
            verify(reportingService).postAnalysisResults(eq(codeAnalysis), any(), anyLong(), anyLong(), any());
            verify(aiAnalysisClient, never()).performAnalysis(any(), any());
        }

        @Test
        @DisplayName("should use pre-acquired lock and skip lock acquisition")
        void shouldUsePreAcquiredLockAndSkipLockAcquisition() throws Exception {
            PrProcessRequest request = createRequest();
            request.preAcquiredLockKey = "pre-lock-key-999";
            PullRequestAnalysisProcessor.EventConsumer consumer = mock(PullRequestAnalysisProcessor.EventConsumer.class);

            when(project.getEffectiveVcsConnection()).thenReturn(vcsConnection);
            when(project.getId()).thenReturn(1L);
            when(project.getName()).thenReturn("Test Project");
            when(vcsConnection.getProviderType()).thenReturn(EVcsProvider.BITBUCKET_CLOUD);

            when(pullRequestService.createOrUpdatePullRequest(anyLong(), anyLong(), anyString(), anyString(), anyString(), any()))
                    .thenReturn(pullRequest);
            when(vcsServiceFactory.getReportingService(EVcsProvider.BITBUCKET_CLOUD)).thenReturn(reportingService);
            when(vcsServiceFactory.getAiClientService(EVcsProvider.BITBUCKET_CLOUD)).thenReturn(aiClientService);
            when(codeAnalysisService.getCodeAnalysisCache(anyLong(), anyString(), anyLong())).thenReturn(Optional.empty());
            when(codeAnalysisService.getAnalysisByCommitHash(anyLong(), anyString())).thenReturn(Optional.empty());
            when(codeAnalysisService.getAllPrAnalyses(anyLong(), anyLong())).thenReturn(List.of());
            when(aiClientService.buildAiAnalysisRequest(any(), any(), any(), anyList())).thenReturn(aiAnalysisRequest);
            when(aiAnalysisRequest.getRawDiff()).thenReturn("");
            Map<String, Object> aiResponse = Map.of("comment", "Review", "issues", List.of());
            when(aiAnalysisClient.performAnalysis(any(), any())).thenReturn(aiResponse);
            when(codeAnalysisService.createAnalysisFromAiResponse(any(), any(), anyLong(), anyString(), anyString(), anyString(), any(), any(), any()))
                    .thenReturn(codeAnalysis);

            processor.process(request, consumer, project);

            // Should NOT call acquireLockWithWait since we have pre-acquired lock
            verify(analysisLockService, never()).acquireLockWithWait(any(), anyString(), any(), anyString(), anyLong(), any());
            // Should NOT release lock (pre-acquired locks are released by caller)
            verify(analysisLockService, never()).releaseLock(anyString());
        }

        @Test
        @DisplayName("should return cached_by_commit when commit hash cache hits")
        void shouldReturnCachedByCommitWhenCommitHashCacheHits() throws Exception {
            PrProcessRequest request = createRequest();
            PullRequestAnalysisProcessor.EventConsumer consumer = mock(PullRequestAnalysisProcessor.EventConsumer.class);

            when(project.getEffectiveVcsConnection()).thenReturn(vcsConnection);
            when(project.getId()).thenReturn(1L);
            when(project.getName()).thenReturn("Test Project");
            when(vcsConnection.getProviderType()).thenReturn(EVcsProvider.BITBUCKET_CLOUD);

            when(analysisLockService.acquireLockWithWait(any(), anyString(), any(), anyString(), anyLong(), any()))
                    .thenReturn(Optional.of("lock-key"));
            when(pullRequestService.createOrUpdatePullRequest(anyLong(), anyLong(), anyString(), anyString(), anyString(), any()))
                    .thenReturn(pullRequest);
            when(pullRequest.getId()).thenReturn(100L);
            when(vcsServiceFactory.getReportingService(EVcsProvider.BITBUCKET_CLOUD)).thenReturn(reportingService);

            // No exact cache match, but commit hash matches from another PR
            when(codeAnalysisService.getCodeAnalysisCache(anyLong(), anyString(), anyLong())).thenReturn(Optional.empty());
            CodeAnalysis sourceAnalysis = mock(CodeAnalysis.class);
            when(sourceAnalysis.getPrNumber()).thenReturn(99L);
            when(sourceAnalysis.getDiffFingerprint()).thenReturn("fp123");
            when(codeAnalysisService.getAnalysisByCommitHash(1L, "abc123")).thenReturn(Optional.of(sourceAnalysis));

            CodeAnalysis clonedAnalysis = mock(CodeAnalysis.class);
            when(codeAnalysisService.cloneAnalysisForPr(any(), any(), anyLong(), anyString(), anyString(), anyString(), anyString()))
                    .thenReturn(clonedAnalysis);

            Map<String, Object> result = processor.process(request, consumer, project);

            assertThat(result).containsEntry("status", "cached_by_commit");
            assertThat(result).containsEntry("cached", true);
            verify(codeAnalysisService).cloneAnalysisForPr(eq(sourceAnalysis), eq(project), eq(42L), eq("abc123"), eq("main"), eq("feature-branch"), eq("fp123"));
            verify(reportingService).postAnalysisResults(eq(clonedAnalysis), any(), anyLong(), any(), any());
            verify(analysisLockService).releaseLock("lock-key");
        }

        @Test
        @DisplayName("should return cached_by_fingerprint when diff fingerprint matches")
        void shouldReturnCachedByFingerprintWhenDiffFingerprintMatches() throws Exception {
            PrProcessRequest request = createRequest();
            PullRequestAnalysisProcessor.EventConsumer consumer = mock(PullRequestAnalysisProcessor.EventConsumer.class);

            when(project.getEffectiveVcsConnection()).thenReturn(vcsConnection);
            when(project.getId()).thenReturn(1L);
            when(project.getName()).thenReturn("Test Project");
            when(vcsConnection.getProviderType()).thenReturn(EVcsProvider.BITBUCKET_CLOUD);

            when(analysisLockService.acquireLockWithWait(any(), anyString(), any(), anyString(), anyLong(), any()))
                    .thenReturn(Optional.of("lock-key"));
            when(pullRequestService.createOrUpdatePullRequest(anyLong(), anyLong(), anyString(), anyString(), anyString(), any()))
                    .thenReturn(pullRequest);
            when(pullRequest.getId()).thenReturn(100L);
            when(vcsServiceFactory.getReportingService(EVcsProvider.BITBUCKET_CLOUD)).thenReturn(reportingService);
            when(vcsServiceFactory.getAiClientService(EVcsProvider.BITBUCKET_CLOUD)).thenReturn(aiClientService);

            when(codeAnalysisService.getCodeAnalysisCache(anyLong(), anyString(), anyLong())).thenReturn(Optional.empty());
            when(codeAnalysisService.getAnalysisByCommitHash(anyLong(), anyString())).thenReturn(Optional.empty());
            when(codeAnalysisService.getAllPrAnalyses(anyLong(), anyLong())).thenReturn(List.of());

            when(aiClientService.buildAiAnalysisRequest(any(), any(), any(), anyList())).thenReturn(aiAnalysisRequest);
            // A diff that produces a non-null fingerprint
            when(aiAnalysisRequest.getRawDiff()).thenReturn("+added line\n-removed line\n");

            CodeAnalysis fingerprintSource = mock(CodeAnalysis.class);
            when(fingerprintSource.getPrNumber()).thenReturn(77L);
            when(codeAnalysisService.getAnalysisByDiffFingerprint(eq(1L), anyString()))
                    .thenReturn(Optional.of(fingerprintSource));

            CodeAnalysis clonedAnalysis = mock(CodeAnalysis.class);
            when(codeAnalysisService.cloneAnalysisForPr(any(), any(), anyLong(), anyString(), anyString(), anyString(), anyString()))
                    .thenReturn(clonedAnalysis);

            Map<String, Object> result = processor.process(request, consumer, project);

            assertThat(result).containsEntry("status", "cached_by_fingerprint");
            assertThat(result).containsEntry("cached", true);
            verify(analysisLockService).releaseLock("lock-key");
        }

        @Test
        @DisplayName("should handle IOException during analysis gracefully")
        void shouldHandleIOExceptionDuringAnalysis() throws Exception {
            PrProcessRequest request = createRequest();
            PullRequestAnalysisProcessor.EventConsumer consumer = mock(PullRequestAnalysisProcessor.EventConsumer.class);

            when(project.getEffectiveVcsConnection()).thenReturn(vcsConnection);
            when(project.getId()).thenReturn(1L);
            when(project.getName()).thenReturn("Test Project");
            when(vcsConnection.getProviderType()).thenReturn(EVcsProvider.BITBUCKET_CLOUD);

            when(analysisLockService.acquireLockWithWait(any(), anyString(), any(), anyString(), anyLong(), any()))
                    .thenReturn(Optional.of("lock-key"));
            when(pullRequestService.createOrUpdatePullRequest(anyLong(), anyLong(), anyString(), anyString(), anyString(), any()))
                    .thenReturn(pullRequest);
            when(vcsServiceFactory.getReportingService(EVcsProvider.BITBUCKET_CLOUD)).thenReturn(reportingService);
            when(vcsServiceFactory.getAiClientService(EVcsProvider.BITBUCKET_CLOUD)).thenReturn(aiClientService);

            when(codeAnalysisService.getCodeAnalysisCache(anyLong(), anyString(), anyLong())).thenReturn(Optional.empty());
            when(codeAnalysisService.getAnalysisByCommitHash(anyLong(), anyString())).thenReturn(Optional.empty());
            when(codeAnalysisService.getAllPrAnalyses(anyLong(), anyLong())).thenReturn(List.of());
            when(aiClientService.buildAiAnalysisRequest(any(), any(), any(), anyList())).thenReturn(aiAnalysisRequest);
            when(aiAnalysisRequest.getRawDiff()).thenReturn("");

            when(aiAnalysisClient.performAnalysis(any(), any())).thenThrow(new IOException("AI service down"));

            Map<String, Object> result = processor.process(request, consumer, project);

            assertThat(result).containsEntry("status", "error");
            assertThat(result.get("message").toString()).contains("AI service down");
            verify(consumer).accept(argThat(event ->
                    "error".equals(event.get("type")) && event.get("message").toString().contains("I/O error")));
            verify(analysisLockService).releaseLock("lock-key");
        }

        @Test
        @DisplayName("should handle IOException when posting results to VCS")
        void shouldHandleIOExceptionWhenPostingResults() throws Exception {
            PrProcessRequest request = createRequest();
            PullRequestAnalysisProcessor.EventConsumer consumer = mock(PullRequestAnalysisProcessor.EventConsumer.class);

            when(project.getEffectiveVcsConnection()).thenReturn(vcsConnection);
            when(project.getId()).thenReturn(1L);
            when(project.getName()).thenReturn("Test Project");
            when(vcsConnection.getProviderType()).thenReturn(EVcsProvider.BITBUCKET_CLOUD);

            when(analysisLockService.acquireLockWithWait(any(), anyString(), any(), anyString(), anyLong(), any()))
                    .thenReturn(Optional.of("lock-key"));
            when(pullRequestService.createOrUpdatePullRequest(anyLong(), anyLong(), anyString(), anyString(), anyString(), any()))
                    .thenReturn(pullRequest);
            when(pullRequest.getId()).thenReturn(100L);
            when(vcsServiceFactory.getReportingService(EVcsProvider.BITBUCKET_CLOUD)).thenReturn(reportingService);
            when(vcsServiceFactory.getAiClientService(EVcsProvider.BITBUCKET_CLOUD)).thenReturn(aiClientService);
            when(codeAnalysisService.getCodeAnalysisCache(anyLong(), anyString(), anyLong())).thenReturn(Optional.empty());
            when(codeAnalysisService.getAnalysisByCommitHash(anyLong(), anyString())).thenReturn(Optional.empty());
            when(codeAnalysisService.getAllPrAnalyses(anyLong(), anyLong())).thenReturn(List.of());
            when(aiClientService.buildAiAnalysisRequest(any(), any(), any(), anyList())).thenReturn(aiAnalysisRequest);
            when(aiAnalysisRequest.getRawDiff()).thenReturn("");
            when(aiAnalysisRequest.getChangedFiles()).thenReturn(List.of("file.java"));

            Map<String, Object> aiResponse = Map.of("comment", "Review", "issues", List.of());
            when(aiAnalysisClient.performAnalysis(any(), any())).thenReturn(aiResponse);
            when(codeAnalysisService.createAnalysisFromAiResponse(any(), any(), anyLong(), anyString(), anyString(), anyString(), any(), any(), any()))
                    .thenReturn(codeAnalysis);
            doThrow(new IOException("VCS API error")).when(reportingService)
                    .postAnalysisResults(any(), any(), anyLong(), any(), any());

            Map<String, Object> result = processor.process(request, consumer, project);

            // Should still return AI response despite posting failure
            assertThat(result).containsKey("comment");
            verify(consumer).accept(argThat(event ->
                    "warning".equals(event.get("type"))));
        }

        @Test
        @DisplayName("should handle IOException when posting commit-hash cached results")
        void shouldHandleIOExceptionWhenPostingCommitHashCachedResults() throws Exception {
            PrProcessRequest request = createRequest();
            PullRequestAnalysisProcessor.EventConsumer consumer = mock(PullRequestAnalysisProcessor.EventConsumer.class);

            when(project.getEffectiveVcsConnection()).thenReturn(vcsConnection);
            when(project.getId()).thenReturn(1L);
            when(project.getName()).thenReturn("Test Project");
            when(vcsConnection.getProviderType()).thenReturn(EVcsProvider.BITBUCKET_CLOUD);

            when(analysisLockService.acquireLockWithWait(any(), anyString(), any(), anyString(), anyLong(), any()))
                    .thenReturn(Optional.of("lock-key"));
            when(pullRequestService.createOrUpdatePullRequest(anyLong(), anyLong(), anyString(), anyString(), anyString(), any()))
                    .thenReturn(pullRequest);
            when(pullRequest.getId()).thenReturn(100L);
            when(vcsServiceFactory.getReportingService(EVcsProvider.BITBUCKET_CLOUD)).thenReturn(reportingService);

            when(codeAnalysisService.getCodeAnalysisCache(anyLong(), anyString(), anyLong())).thenReturn(Optional.empty());
            CodeAnalysis sourceAnalysis = mock(CodeAnalysis.class);
            when(sourceAnalysis.getPrNumber()).thenReturn(99L);
            when(sourceAnalysis.getDiffFingerprint()).thenReturn("fp");
            when(codeAnalysisService.getAnalysisByCommitHash(1L, "abc123")).thenReturn(Optional.of(sourceAnalysis));
            CodeAnalysis clonedAnalysis = mock(CodeAnalysis.class);
            when(codeAnalysisService.cloneAnalysisForPr(any(), any(), anyLong(), anyString(), anyString(), anyString(), any()))
                    .thenReturn(clonedAnalysis);
            doThrow(new IOException("Post fail")).when(reportingService).postAnalysisResults(any(), any(), anyLong(), any(), any());

            Map<String, Object> result = processor.process(request, consumer, project);

            // Should still return cached result despite posting failure
            assertThat(result).containsEntry("status", "cached_by_commit");
            assertThat(result).containsEntry("cached", true);
        }
    }

    @Nested
    @DisplayName("postAnalysisCacheIfExist()")
    class PostAnalysisCacheIfExistTests {

        @Test
        @DisplayName("should return true and post when cache exists")
        void shouldReturnTrueAndPostWhenCacheExists() throws IOException {
            when(project.getId()).thenReturn(1L);
            when(codeAnalysisService.getCodeAnalysisCache(1L, "abc123", 42L))
                    .thenReturn(Optional.of(codeAnalysis));
            when(pullRequest.getId()).thenReturn(100L);

            boolean result = processor.postAnalysisCacheIfExist(
                    project, pullRequest, "abc123", 42L, reportingService, "placeholder-id"
            );

            assertThat(result).isTrue();
            verify(reportingService).postAnalysisResults(eq(codeAnalysis), eq(project), eq(42L), eq(100L), eq("placeholder-id"));
        }

        @Test
        @DisplayName("should return false when no cache exists")
        void shouldReturnFalseWhenNoCacheExists() throws IOException {
            when(project.getId()).thenReturn(1L);
            when(codeAnalysisService.getCodeAnalysisCache(1L, "abc123", 42L))
                    .thenReturn(Optional.empty());

            boolean result = processor.postAnalysisCacheIfExist(
                    project, pullRequest, "abc123", 42L, reportingService, "placeholder-id"
            );

            assertThat(result).isFalse();
            verify(reportingService, never()).postAnalysisResults(any(), any(), anyLong(), any(), any());
        }

        @Test
        @DisplayName("should return true even when posting fails")
        void shouldReturnTrueEvenWhenPostingFails() throws IOException {
            when(project.getId()).thenReturn(1L);
            when(codeAnalysisService.getCodeAnalysisCache(1L, "abc123", 42L))
                    .thenReturn(Optional.of(codeAnalysis));
            when(pullRequest.getId()).thenReturn(100L);
            doThrow(new IOException("Post error")).when(reportingService).postAnalysisResults(any(), any(), anyLong(), any(), any());

            boolean result = processor.postAnalysisCacheIfExist(
                    project, pullRequest, "abc123", 42L, reportingService, "placeholder-id"
            );

            // Should still return true (cache existed)
            assertThat(result).isTrue();
        }
    }

    @Nested
    @DisplayName("Constructor")
    class ConstructorTests {

        @Test
        @DisplayName("should work without optional dependencies")
        void shouldWorkWithoutOptionalDependencies() {
            PullRequestAnalysisProcessor processorWithoutOptional = new PullRequestAnalysisProcessor(
                    pullRequestService,
                    codeAnalysisService,
                    aiAnalysisClient,
                    vcsServiceFactory,
                    analysisLockService,
                    null, // ragOperationsService
                    null  // eventPublisher
            );

            assertThat(processorWithoutOptional).isNotNull();
        }
    }

    @Nested
    @DisplayName("VCS Provider")
    class VcsProviderTests {

        @Test
        @DisplayName("should throw when no VCS connection configured")
        void shouldThrowWhenNoVcsConnectionConfigured() {
            PrProcessRequest request = createRequest();
            PullRequestAnalysisProcessor.EventConsumer consumer = mock(PullRequestAnalysisProcessor.EventConsumer.class);

            when(project.getId()).thenReturn(1L);
            when(project.getName()).thenReturn("Test Project");
            when(project.getEffectiveVcsConnection()).thenReturn(null);

            when(analysisLockService.acquireLockWithWait(any(), anyString(), any(), anyString(), anyLong(), any()))
                    .thenReturn(Optional.of("lock-key-123"));
            when(pullRequestService.createOrUpdatePullRequest(anyLong(), anyLong(), anyString(), anyString(), anyString(), any()))
                    .thenReturn(pullRequest);

            assertThatThrownBy(() -> processor.process(request, consumer, project))
                    .isInstanceOf(IllegalStateException.class)
                    .hasMessageContaining("No VCS connection configured");
        }
    }
}
