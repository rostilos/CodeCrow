package org.rostilos.codecrow.pipelineagent.characterization.p002;

import com.fasterxml.jackson.databind.ObjectMapper;
import com.fasterxml.jackson.databind.node.ObjectNode;
import org.junit.jupiter.api.BeforeEach;
import org.junit.jupiter.api.Tag;
import org.junit.jupiter.api.Test;
import org.junit.jupiter.api.extension.ExtendWith;
import org.mockito.Mock;
import org.mockito.junit.jupiter.MockitoExtension;
import org.rostilos.codecrow.analysisapi.rag.RagOperationsService;
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
import java.util.function.Consumer;

import static org.assertj.core.api.Assertions.assertThat;
import static org.mockito.ArgumentMatchers.*;
import static org.mockito.Mockito.*;

@Tag("legacy-defect")
@ExtendWith(MockitoExtension.class)
class WebhookCoalescingLegacyCharacterizationTest {

    private static final ObjectMapper JSON = new ObjectMapper();

    @Mock private PullRequestAnalysisProcessor pullRequestAnalysisProcessor;
    @Mock private BranchAnalysisProcessor branchAnalysisProcessor;
    @Mock private VcsServiceFactory vcsServiceFactory;
    @Mock private AnalysisLockService analysisLockService;
    @Mock private PullRequestService pullRequestService;
    @Mock private RagOperationsService ragOperationsService;
    @Mock private Project project;

    private Consumer<Map<String, Object>> events;

    @BeforeEach
    @SuppressWarnings("unchecked")
    void setUp() {
        events = mock(Consumer.class);
        lenient().when(project.getId()).thenReturn(1L);
        lenient().when(project.hasVcsBinding()).thenReturn(true);
        lenient().when(project.getAiBinding()).thenReturn(mock(ProjectAiConnectionBinding.class));
        lenient().when(project.isPrAnalysisEnabled()).thenReturn(true);
        lenient().when(project.getConfiguration()).thenReturn(null);
        lenient().when(analysisLockService.acquireLock(
                eq(project), eq("feature"), eq(AnalysisLockType.PR_ANALYSIS), anyString(), eq(42L)))
                .thenReturn(Optional.empty());
    }

    @Test
    void legacyDefectGitHubNewerHeadIsIgnoredWhileOlderHeadOwnsTheLock() {
        GitHubPullRequestWebhookHandler handler = githubHandler();

        WebhookResult result = handler.handle(
                payload(EVcsProvider.GITHUB, "pull_request", "synchronize", "head-b"),
                project,
                events);

        assertIgnoredWithoutEnqueue(result);
    }

    @Test
    void legacyDefectGitLabNewerHeadIsIgnoredWhileOlderHeadOwnsTheLock() {
        GitLabMergeRequestWebhookHandler handler = gitlabHandler();

        WebhookResult result = handler.handle(
                payload(EVcsProvider.GITLAB, "merge_request", "update", "head-b"),
                project,
                events);

        assertIgnoredWithoutEnqueue(result);
    }

    @Test
    void legacyDefectBitbucketNewerHeadIsIgnoredWhileOlderHeadOwnsTheLock() {
        BitbucketCloudPullRequestWebhookHandler handler = bitbucketHandler();

        WebhookResult result = handler.handle(
                payload(EVcsProvider.BITBUCKET_CLOUD, "pullrequest:updated", null, "head-b"),
                project,
                events);

        assertIgnoredWithoutEnqueue(result);
    }

    @Test
    void legacyDefectFreshHandlerAfterProcessRestartHasNoDurableCoalescedHead() {
        WebhookResult beforeRestart = githubHandler().handle(
                payload(EVcsProvider.GITHUB, "pull_request", "synchronize", "head-b"),
                project,
                events);
        WebhookResult afterRestart = githubHandler().handle(
                payload(EVcsProvider.GITHUB, "pull_request", "synchronize", "head-c"),
                project,
                events);

        assertThat(beforeRestart.status()).isEqualTo("ignored");
        assertThat(afterRestart.status()).isEqualTo("ignored");
        verify(analysisLockService, times(2)).acquireLock(
                eq(project), eq("feature"), eq(AnalysisLockType.PR_ANALYSIS), anyString(), eq(42L));
        verifyNoInteractions(pullRequestAnalysisProcessor);
    }

    private void assertIgnoredWithoutEnqueue(WebhookResult result) {
        assertThat(result.success()).isTrue();
        assertThat(result.status()).isEqualTo("ignored");
        assertThat(result.message()).contains("already in progress");
        verifyNoInteractions(pullRequestAnalysisProcessor);
    }

    private GitHubPullRequestWebhookHandler githubHandler() {
        return new GitHubPullRequestWebhookHandler(
                pullRequestAnalysisProcessor,
                branchAnalysisProcessor,
                vcsServiceFactory,
                analysisLockService,
                pullRequestService,
                ragOperationsService,
                false);
    }

    private GitLabMergeRequestWebhookHandler gitlabHandler() {
        return new GitLabMergeRequestWebhookHandler(
                pullRequestAnalysisProcessor,
                vcsServiceFactory,
                analysisLockService,
                pullRequestService,
                ragOperationsService,
                false);
    }

    private BitbucketCloudPullRequestWebhookHandler bitbucketHandler() {
        return new BitbucketCloudPullRequestWebhookHandler(
                pullRequestAnalysisProcessor,
                vcsServiceFactory,
                analysisLockService,
                pullRequestService,
                ragOperationsService,
                false);
    }

    private WebhookPayload payload(EVcsProvider provider, String eventType, String action, String head) {
        ObjectNode raw = JSON.createObjectNode();
        if (provider == EVcsProvider.GITHUB) {
            raw.put("action", action);
            raw.putObject("pull_request").put("title", "offline PR");
        } else if (provider == EVcsProvider.GITLAB) {
            raw.putObject("object_attributes").put("action", action);
        }
        return new WebhookPayload(
                provider,
                eventType,
                "repo-id",
                "offline-repository",
                "offline-workspace",
                "42",
                "feature",
                "main",
                head,
                raw,
                null,
                "author-id",
                "author");
    }
}
