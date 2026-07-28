package org.rostilos.codecrow.pipelineagent.generic.webhookhandler;

import org.junit.jupiter.api.AfterEach;
import org.junit.jupiter.api.BeforeEach;
import org.junit.jupiter.api.Test;
import org.junit.jupiter.api.extension.ExtendWith;
import org.mockito.Mock;
import org.mockito.junit.jupiter.MockitoExtension;
import org.rostilos.codecrow.analysisengine.processor.analysis.PullRequestAnalysisProcessor;
import org.rostilos.codecrow.analysisengine.service.PromptSanitizationService;
import org.rostilos.codecrow.analysisengine.util.PromptDryRunMode;
import org.rostilos.codecrow.core.model.codeanalysis.CodeAnalysis;
import org.rostilos.codecrow.core.model.project.Project;
import org.rostilos.codecrow.core.model.project.config.CommentCommandsConfig;
import org.rostilos.codecrow.core.model.project.config.ProjectConfig;
import org.rostilos.codecrow.core.model.vcs.EVcsProvider;
import org.rostilos.codecrow.core.persistence.repository.codeanalysis.PrSummarizeCacheRepository;
import org.rostilos.codecrow.core.service.CodeAnalysisService;
import org.rostilos.codecrow.pipelineagent.generic.dto.webhook.WebhookPayload;
import org.rostilos.codecrow.pipelineagent.generic.service.CommandAuthorizationService;
import org.rostilos.codecrow.pipelineagent.generic.service.CommentCommandRateLimitService;
import org.rostilos.codecrow.pipelineagent.generic.webhookhandler.CommentCommandWebhookHandler.CommentCommandProcessor;
import org.rostilos.codecrow.pipelineagent.generic.webhookhandler.WebhookHandler.WebhookResult;
import org.rostilos.codecrow.vcsclient.VcsClientProvider;
import org.springframework.test.util.ReflectionTestUtils;

import java.util.ArrayList;
import java.util.List;
import java.util.Map;
import java.util.Optional;

import static org.assertj.core.api.Assertions.assertThat;
import static org.mockito.ArgumentMatchers.any;
import static org.mockito.ArgumentMatchers.anyLong;
import static org.mockito.ArgumentMatchers.anyString;
import static org.mockito.ArgumentMatchers.eq;
import static org.mockito.Mockito.mock;
import static org.mockito.Mockito.never;
import static org.mockito.Mockito.verify;
import static org.mockito.Mockito.when;

@ExtendWith(MockitoExtension.class)
class CommentCommandWebhookHandlerAnalyzeTest {

    @Mock private CommentCommandRateLimitService rateLimitService;
    @Mock private PromptSanitizationService sanitizationService;
    @Mock private CodeAnalysisService codeAnalysisService;
    @Mock private PrSummarizeCacheRepository summarizeCacheRepository;
    @Mock private PullRequestAnalysisProcessor pullRequestAnalysisProcessor;
    @Mock private VcsClientProvider vcsClientProvider;
    @Mock private CommandAuthorizationService authorizationService;
    @Mock private CommentCommandProcessor summarizeProcessor;
    @Mock private CommentCommandProcessor askProcessor;
    @Mock private CommentCommandProcessor qaDocProcessor;

    private CommentCommandWebhookHandler handler;
    private Project project;
    private List<Map<String, Object>> events;

    @BeforeEach
    void setUp() {
        handler = new CommentCommandWebhookHandler(
                rateLimitService,
                sanitizationService,
                codeAnalysisService,
                summarizeCacheRepository,
                pullRequestAnalysisProcessor,
                vcsClientProvider,
                authorizationService,
                summarizeProcessor,
                askProcessor,
                qaDocProcessor);

        project = new Project();
        ReflectionTestUtils.setField(project, "id", 1L);
        ProjectConfig config = new ProjectConfig();
        config.setCommentCommands(
                new CommentCommandsConfig(true, null, null, null, null, null, null));
        ReflectionTestUtils.setField(project, "configuration", config);

        events = new ArrayList<>();
        when(rateLimitService.checkRateLimit(project))
                .thenReturn(CommentCommandRateLimitService.RateLimitCheckResult.allowed(100));
        when(authorizationService.checkAuthorization(any(), any(), any(), any()))
                .thenReturn(new CommandAuthorizationService.AuthorizationResult(
                        true, "Authorized"));
    }

    @AfterEach
    void clearDryRunSelection() {
        System.clearProperty(PromptDryRunMode.ENABLED_KEY);
        System.clearProperty(PromptDryRunMode.PROJECT_IDS_KEY);
    }

    @Test
    void dryRunAnalyzeBypassesCommandCacheAndRunsCompleteProcessor() throws Exception {
        System.setProperty(PromptDryRunMode.ENABLED_KEY, "true");
        System.setProperty(PromptDryRunMode.PROJECT_IDS_KEY, "1");
        when(pullRequestAnalysisProcessor.process(any(), any(), eq(project)))
                .thenReturn(Map.of(
                        "dryRun", true,
                        "status", "prompt_capture_completed",
                        "promptArtifact", Map.of("filename", "capture.json")));

        WebhookResult result = handler.handle(analyzePayload(), project, events::add);

        assertThat(result.success()).isTrue();
        assertThat(events).anySatisfy(event -> {
            assertThat(event)
                    .containsEntry("type", "status")
                    .containsEntry("state", "cache_bypassed");
        });
        verify(codeAnalysisService, never()).getCodeAnalysisCache(
                anyLong(), anyString(), anyLong());
        verify(pullRequestAnalysisProcessor).process(any(), any(), eq(project));
    }

    @Test
    void normalAnalyzeRetainsCommandCacheShortcut() throws Exception {
        CodeAnalysis cachedAnalysis = mock(CodeAnalysis.class);
        when(cachedAnalysis.getId()).thenReturn(77L);
        when(codeAnalysisService.getCodeAnalysisCache(1L, "abc123", 42L))
                .thenReturn(Optional.of(cachedAnalysis));

        WebhookResult result = handler.handle(analyzePayload(), project, events::add);

        assertThat(result.success()).isTrue();
        assertThat(result.data())
                .containsEntry("cached", true)
                .containsEntry("analysisId", 77L);
        assertThat(events).anySatisfy(event ->
                assertThat(event).containsEntry("state", "checking_cache"));
        verify(pullRequestAnalysisProcessor, never()).process(any(), any(), any());
    }

    private WebhookPayload analyzePayload() {
        WebhookPayload.CommentData comment = new WebhookPayload.CommentData(
                "comment-1",
                "/codecrow analyze",
                "user-1",
                "johndoe",
                null,
                false,
                null,
                null);
        return new WebhookPayload(
                EVcsProvider.GITHUB,
                "issue_comment",
                "repo-123",
                "my-repo",
                "my-org",
                "42",
                "feature/general-replay",
                "main",
                "abc123",
                null,
                comment,
                "user-1",
                "johndoe");
    }
}
