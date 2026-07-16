package org.rostilos.codecrow.pipelineagent.generic.webhookhandler;

import org.junit.jupiter.api.BeforeEach;
import org.junit.jupiter.api.Test;
import org.junit.jupiter.api.extension.ExtendWith;
import org.mockito.Mock;
import org.mockito.junit.jupiter.MockitoExtension;
import org.rostilos.codecrow.analysisengine.dto.request.processor.PrProcessRequest;
import org.rostilos.codecrow.analysisengine.processor.analysis.PullRequestAnalysisProcessor;
import org.rostilos.codecrow.analysisengine.service.PromptSanitizationService;
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
import static org.mockito.ArgumentMatchers.eq;
import static org.mockito.Mockito.lenient;
import static org.mockito.Mockito.never;
import static org.mockito.Mockito.verify;
import static org.mockito.Mockito.when;

@ExtendWith(MockitoExtension.class)
class CommentCommandWebhookHandlerAnalyzeRetirementTest {

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
        ReflectionTestUtils.setField(project, "id", 7L);
        ProjectConfig config = new ProjectConfig();
        config.setCommentCommands(new CommentCommandsConfig(
                true, null, null, null, null, null, null));
        ReflectionTestUtils.setField(project, "configuration", config);

        when(rateLimitService.checkRateLimit(project))
                .thenReturn(CommentCommandRateLimitService.RateLimitCheckResult.allowed(100));
        when(authorizationService.checkAuthorization(any(), any(), any(), any()))
                .thenReturn(new CommandAuthorizationService.AuthorizationResult(
                        true, "Authorized"));
    }

    @Test
    void analyzeRunsFreshProcessorWorkEvenWhenAnExactHistoricalAnalysisExists()
            throws Exception {
        CodeAnalysis historicalAnalysis = new CodeAnalysis();
        lenient().when(codeAnalysisService.getCodeAnalysisCache(7L, "abc123", 42L))
                .thenReturn(Optional.of(historicalAnalysis));
        when(pullRequestAnalysisProcessor.process(
                any(PrProcessRequest.class),
                any(PullRequestAnalysisProcessor.EventConsumer.class),
                eq(project)))
                .thenReturn(Map.of("analysisId", 99L));

        List<Map<String, Object>> events = new ArrayList<>();
        WebhookResult result = handler.handle(analyzePayload(), project, events::add);

        assertThat(result.success()).isTrue();
        assertThat(result.data())
                .containsEntry("analysisId", 99L)
                .containsEntry("commandType", "analyze")
                .doesNotContainKey("cached");
        assertThat(events)
                .extracting(event -> event.get("state"))
                .contains("starting_analysis")
                .doesNotContain("checking_cache", "cache_hit");
        verify(pullRequestAnalysisProcessor).process(
                any(PrProcessRequest.class),
                any(PullRequestAnalysisProcessor.EventConsumer.class),
                eq(project));
        verify(codeAnalysisService, never())
                .getCodeAnalysisCache(any(), any(), any());
    }

    private static WebhookPayload analyzePayload() {
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
                "feature/recheck",
                "main",
                "abc123",
                null,
                comment,
                "user-1",
                "johndoe");
    }
}
