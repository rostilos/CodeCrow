package org.rostilos.codecrow.pipelineagent.generic.webhookhandler;

import com.fasterxml.jackson.databind.ObjectMapper;
import com.fasterxml.jackson.databind.node.ObjectNode;
import org.junit.jupiter.api.BeforeEach;
import org.junit.jupiter.api.extension.ExtendWith;
import org.junit.jupiter.params.ParameterizedTest;
import org.junit.jupiter.params.provider.EnumSource;
import org.mockito.ArgumentCaptor;
import org.mockito.Mock;
import org.mockito.junit.jupiter.MockitoExtension;
import org.rostilos.codecrow.analysisapi.rag.RagOperationsService;
import org.rostilos.codecrow.analysisengine.dto.request.processor.PrProcessRequest;
import org.rostilos.codecrow.analysisengine.processor.analysis.BranchAnalysisProcessor;
import org.rostilos.codecrow.analysisengine.processor.analysis.PullRequestAnalysisProcessor;
import org.rostilos.codecrow.analysisengine.service.AnalysisLockService;
import org.rostilos.codecrow.analysisengine.service.PullRequestService;
import org.rostilos.codecrow.analysisengine.service.vcs.VcsServiceFactory;
import org.rostilos.codecrow.core.model.analysis.AnalysisLockType;
import org.rostilos.codecrow.core.model.project.Project;
import org.rostilos.codecrow.core.model.project.ProjectAiConnectionBinding;
import org.rostilos.codecrow.core.model.vcs.EVcsProvider;
import org.rostilos.codecrow.pipelineagent.bitbucket.webhookhandler.BitbucketCloudPullRequestWebhookHandler;
import org.rostilos.codecrow.pipelineagent.generic.dto.webhook.WebhookPayload;
import org.rostilos.codecrow.pipelineagent.generic.webhookhandler.WebhookHandler.WebhookResult;
import org.rostilos.codecrow.pipelineagent.github.webhookhandler.GitHubPullRequestWebhookHandler;
import org.rostilos.codecrow.pipelineagent.gitlab.webhookhandler.GitLabMergeRequestWebhookHandler;

import java.util.Map;
import java.util.Optional;

import static org.assertj.core.api.Assertions.assertThat;
import static org.mockito.ArgumentMatchers.any;
import static org.mockito.ArgumentMatchers.eq;
import static org.mockito.Mockito.lenient;
import static org.mockito.Mockito.never;
import static org.mockito.Mockito.verify;
import static org.mockito.Mockito.verifyNoInteractions;
import static org.mockito.Mockito.when;

@ExtendWith(MockitoExtension.class)
class LatestHeadWebhookIntakeContractTest {

    private static final ObjectMapper JSON = new ObjectMapper();

    @Mock private PullRequestAnalysisProcessor pullRequestAnalysisProcessor;
    @Mock private BranchAnalysisProcessor branchAnalysisProcessor;
    @Mock private VcsServiceFactory vcsServiceFactory;
    @Mock private AnalysisLockService analysisLockService;
    @Mock private PullRequestService pullRequestService;
    @Mock private RagOperationsService ragOperationsService;
    @Mock private Project project;

    @BeforeEach
    void setUpProject() {
        lenient().when(project.getId()).thenReturn(1L);
        lenient().when(project.hasVcsBinding()).thenReturn(true);
        lenient().when(project.getAiBinding()).thenReturn(
                org.mockito.Mockito.mock(ProjectAiConnectionBinding.class));
        lenient().when(project.isPrAnalysisEnabled()).thenReturn(true);
        lenient().when(project.getConfiguration()).thenReturn(null);
    }

    @ParameterizedTest
    @EnumSource(ProviderCase.class)
    void enabledLatestHeadIntakeDelegatesLockAndPlaceholderOwnershipToProcessor(
            ProviderCase provider) throws Exception {
        when(pullRequestAnalysisProcessor.process(
                any(PrProcessRequest.class),
                any(PullRequestAnalysisProcessor.EventConsumer.class),
                eq(project)))
                .thenReturn(Map.of("status", "completed"));

        WebhookResult result = handler(provider, true).handle(
                payload(provider, "head-b"), project, ignored -> { });

        assertThat(result.success()).isTrue();
        assertThat(result.status()).isEqualTo("processed");
        ArgumentCaptor<PrProcessRequest> request =
                ArgumentCaptor.forClass(PrProcessRequest.class);
        verify(pullRequestAnalysisProcessor).process(
                request.capture(),
                any(PullRequestAnalysisProcessor.EventConsumer.class),
                eq(project));
        assertThat(request.getValue().getCommitHash()).isEqualTo("head-b");
        assertThat(request.getValue().getPreAcquiredLockKey()).isNull();
        assertThat(request.getValue().getPlaceholderCommentId()).isNull();
        verifyNoInteractions(analysisLockService, vcsServiceFactory);
    }

    @ParameterizedTest
    @EnumSource(ProviderCase.class)
    void disabledLatestHeadIntakePreservesLegacyEarlyLockRejection(
            ProviderCase provider) throws Exception {
        when(analysisLockService.acquireLock(
                eq(project),
                eq("feature"),
                eq(AnalysisLockType.PR_ANALYSIS),
                eq("head-b"),
                eq(42L)))
                .thenReturn(Optional.empty());

        WebhookResult result = handler(provider, false).handle(
                payload(provider, "head-b"), project, ignored -> { });

        assertThat(result.success()).isTrue();
        assertThat(result.status()).isEqualTo("ignored");
        assertThat(result.message()).contains("already in progress");
        verify(analysisLockService).acquireLock(
                eq(project),
                eq("feature"),
                eq(AnalysisLockType.PR_ANALYSIS),
                eq("head-b"),
                eq(42L));
        verify(pullRequestAnalysisProcessor, never()).process(
                any(), any(), any());
        verifyNoInteractions(vcsServiceFactory);
    }

    private WebhookHandler handler(ProviderCase provider, boolean latestHeadEnabled) {
        return switch (provider) {
            case GITHUB -> new GitHubPullRequestWebhookHandler(
                    pullRequestAnalysisProcessor,
                    branchAnalysisProcessor,
                    vcsServiceFactory,
                    analysisLockService,
                    pullRequestService,
                    ragOperationsService,
                    latestHeadEnabled);
            case GITLAB -> new GitLabMergeRequestWebhookHandler(
                    pullRequestAnalysisProcessor,
                    vcsServiceFactory,
                    analysisLockService,
                    pullRequestService,
                    ragOperationsService,
                    latestHeadEnabled);
            case BITBUCKET -> new BitbucketCloudPullRequestWebhookHandler(
                    pullRequestAnalysisProcessor,
                    vcsServiceFactory,
                    analysisLockService,
                    pullRequestService,
                    ragOperationsService,
                    latestHeadEnabled);
        };
    }

    private WebhookPayload payload(ProviderCase provider, String head) {
        ObjectNode raw = JSON.createObjectNode();
        String eventType;
        EVcsProvider vcsProvider;
        switch (provider) {
            case GITHUB -> {
                vcsProvider = EVcsProvider.GITHUB;
                eventType = "pull_request";
                raw.put("action", "synchronize");
                raw.putObject("pull_request").put("title", "latest-head contract");
            }
            case GITLAB -> {
                vcsProvider = EVcsProvider.GITLAB;
                eventType = "merge_request";
                raw.putObject("object_attributes").put("action", "update");
            }
            case BITBUCKET -> {
                vcsProvider = EVcsProvider.BITBUCKET_CLOUD;
                eventType = "pullrequest:updated";
            }
            default -> throw new IllegalStateException("Unexpected provider: " + provider);
        }
        return new WebhookPayload(
                vcsProvider,
                eventType,
                "repo-id",
                "repository",
                "workspace",
                "42",
                "feature",
                "main",
                head,
                raw,
                null,
                "author-id",
                "author");
    }

    private enum ProviderCase {
        GITHUB,
        GITLAB,
        BITBUCKET
    }
}
