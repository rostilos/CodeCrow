package org.rostilos.codecrow.pipelineagent.generic.processor;

import org.junit.jupiter.api.BeforeEach;
import org.junit.jupiter.api.AfterEach;
import org.junit.jupiter.api.Test;
import org.junit.jupiter.params.ParameterizedTest;
import org.junit.jupiter.params.provider.EnumSource;
import org.rostilos.codecrow.analysisengine.service.vcs.VcsReportingService;
import org.rostilos.codecrow.pipelineagent.generic.utils.CommentPlaceholders;
import static org.mockito.Mockito.*;
import static org.mockito.ArgumentMatchers.*;
import static org.assertj.core.api.Assertions.*;
import org.junit.jupiter.api.extension.ExtendWith;
import org.mockito.Mock;
import org.mockito.junit.jupiter.MockitoExtension;
import org.rostilos.codecrow.analysisengine.service.branch.BranchAnalysisGateService;
import org.rostilos.codecrow.analysisengine.service.vcs.VcsServiceFactory;
import org.rostilos.codecrow.analysisengine.util.PromptDryRunMode;
import org.rostilos.codecrow.analysisapi.rag.RagOperationsService;
import org.rostilos.codecrow.core.model.job.Job;
import org.rostilos.codecrow.core.model.job.JobType;
import org.rostilos.codecrow.core.model.project.Project;
import org.rostilos.codecrow.core.model.vcs.EVcsProvider;
import org.rostilos.codecrow.core.persistence.repository.project.ProjectRepository;
import org.rostilos.codecrow.core.service.JobService;
import org.rostilos.codecrow.pipelineagent.generic.dto.webhook.WebhookPayload;
import org.rostilos.codecrow.pipelineagent.generic.webhookhandler.WebhookHandler;
import org.springframework.test.util.ReflectionTestUtils;

import java.util.Optional;
import java.util.Map;

import static org.mockito.ArgumentMatchers.any;
import static org.mockito.Mockito.never;
import static org.mockito.Mockito.verify;
import static org.mockito.Mockito.verifyNoInteractions;
import static org.mockito.Mockito.when;

@ExtendWith(MockitoExtension.class)
class WebhookAsyncProcessorBranchGateTest {

    @Mock private ProjectRepository projectRepository;
    @Mock private JobService jobService;
    @Mock private VcsServiceFactory vcsServiceFactory;
    @Mock private BranchAnalysisGateService branchAnalysisGateService;
    @Mock private RagOperationsService ragOperationsService;
    @Mock private WebhookHandler handler;
    @Mock private Project project;

    private WebhookAsyncProcessor processor;

    @BeforeEach
    void setUp() {
        System.setProperty(PromptDryRunMode.ENABLED_KEY, "false");
        System.clearProperty(PromptDryRunMode.PROJECT_IDS_KEY);
        processor = new WebhookAsyncProcessor(
                projectRepository, jobService, vcsServiceFactory,
                branchAnalysisGateService, ragOperationsService);
        ReflectionTestUtils.setField(processor, "self", processor);
    }

    @AfterEach
    void tearDown() {
        System.clearProperty(PromptDryRunMode.ENABLED_KEY);
        System.clearProperty(PromptDryRunMode.PROJECT_IDS_KEY);
    }

    @ParameterizedTest
    @EnumSource(value = EVcsProvider.class, names = {"BITBUCKET_CLOUD", "GITHUB", "GITLAB"})
    void cachedCommandsOnlyCleanTheirTemporaryCommentAfterProcessorPublication(EVcsProvider provider) throws Exception {
        var reporting = mock(VcsReportingService.class);
        when(vcsServiceFactory.getReportingService(provider)).thenReturn(reporting);
        when(projectRepository.findById(1L)).thenReturn(Optional.of(project));
        when(jobService.claimWebhookJobForExecution(any())).thenReturn(true);
        when(reporting.postComment(eq(project), eq(42L), anyString(), anyString())).thenReturn("temporary");
        for (String command : java.util.List.of("analyze", "review")) {
            var job = new Job(); job.setJobType(command.equals("analyze") ? JobType.ANALYZE_COMMAND : JobType.REVIEW_COMMAND);
            var payload = new WebhookPayload(provider, "comment", "repo-id", "repo", "owner", "42", "feature", "main", "head", null,
                    new WebhookPayload.CommentData("command", "/codecrow " + command, "user", "user", null, false, null, null), "user", "user");
            when(handler.handle(eq(payload), eq(project), any())).thenReturn(WebhookHandler.WebhookResult.success("republished",
                    Map.of("cached", true, "commandType", command)));
            processor.processWebhookInTransaction(provider, 1L, payload, handler, job);
            verify(jobService).completeJob(job);
        }
        assertThat(CommentPlaceholders.getMarkerForCommandType("review")).isNotEqualTo("<!-- codecrow-review -->");
        verify(reporting).deleteCommentsByMarker(project, 42L, CommentPlaceholders.getMarkerForCommandType("analyze"));
        verify(reporting).deleteCommentsByMarker(project, 42L, CommentPlaceholders.getMarkerForCommandType("review"));
        verify(reporting, times(2)).postComment(eq(project), eq(42L), anyString(), anyString());
        verify(reporting, times(2)).deleteComment(project, 42L, "temporary");
        verifyNoMoreInteractions(reporting);
    }

    @ParameterizedTest
    @EnumSource(value = EVcsProvider.class, names = {"BITBUCKET_CLOUD", "GITHUB", "GITLAB"})
    void automaticPlaceholderDoesNotDeleteTheLastReport(EVcsProvider provider) throws Exception {
        var reporting = mock(VcsReportingService.class);
        when(vcsServiceFactory.getReportingService(provider)).thenReturn(reporting);
        Class<?> handlerClass = switch (provider) {
            case BITBUCKET_CLOUD -> org.rostilos.codecrow.pipelineagent.bitbucket.webhookhandler.BitbucketCloudPullRequestWebhookHandler.class;
            case GITHUB -> org.rostilos.codecrow.pipelineagent.github.webhookhandler.GitHubPullRequestWebhookHandler.class;
            case GITLAB -> org.rostilos.codecrow.pipelineagent.gitlab.webhookhandler.GitLabMergeRequestWebhookHandler.class;
            default -> throw new IllegalArgumentException();
        };
        Object automaticHandler = mock(handlerClass, CALLS_REAL_METHODS);
        ReflectionTestUtils.setField(automaticHandler, "vcsServiceFactory", vcsServiceFactory);
        when(reporting.postComment(eq(project), eq(42L), anyString(), anyString())).thenReturn("temporary");
        assertThat((String) ReflectionTestUtils.invokeMethod(automaticHandler, "postPlaceholderComment", project, 42L))
                .isEqualTo("temporary");
        verify(reporting).postComment(eq(project), eq(42L), anyString(), anyString());
        verifyNoMoreInteractions(reporting);
    }

    @Test
    void duplicateDispatchDoesNotReachProviderHandler() {
        Job job = new Job();
        ReflectionTestUtils.setField(job, "id", 100L);
        job.setJobType(JobType.PR_ANALYSIS);
        WebhookPayload payload = new WebhookPayload(
                EVcsProvider.GITHUB, "pull_request", "repo-id", "repo", "owner",
                "42", "feature", "main", "head", null);
        processor.processWebhookInTransaction(EVcsProvider.GITHUB, 1L, payload, handler, job);
        verify(jobService).claimWebhookJobForExecution(job);
        verifyNoInteractions(handler, projectRepository, branchAnalysisGateService, ragOperationsService);
    }

    @Test
    void supersededBranchJobNeverInvokesProviderHandler() {
        Job branchJob = new Job();
        ReflectionTestUtils.setField(branchJob, "id", 101L);
        branchJob.setProject(project);
        branchJob.setJobType(JobType.BRANCH_ANALYSIS);
        branchJob.setBranchName("main");
        branchJob.setPrNumber(41L);

        WebhookPayload payload = new WebhookPayload(
                EVcsProvider.BITBUCKET_CLOUD, "pullrequest:fulfilled", "repo-id", "repo", "owner",
                "41", "feature/one", "main", "merge-1", null);

        when(jobService.claimWebhookJobForExecution(any())).thenReturn(true);
        when(projectRepository.findById(1L)).thenReturn(Optional.of(project));
        when(branchAnalysisGateService.awaitDependencies(
                org.mockito.ArgumentMatchers.eq(1L),
                org.mockito.ArgumentMatchers.eq(branchJob),
                any()))
                .thenReturn(BranchAnalysisGateService.GateResult.SUPERSEDED);

        processor.processWebhookInTransaction(
                EVcsProvider.GITHUB, 1L, payload, handler, branchJob);

        verify(jobService).claimWebhookJobForExecution(branchJob);
        verify(jobService).skipJob(
                org.mockito.ArgumentMatchers.eq(branchJob),
                org.mockito.ArgumentMatchers.contains("Superseded"));
        verify(handler, never()).handle(any(), any(), any());
        verify(jobService, never()).completeJob(any(Job.class));
        verifyNoInteractions(ragOperationsService);
    }

    @Test
    void prJobPassesTargetBranchDependencyGateBeforeProviderHandler() {
        Job prJob = new Job();
        ReflectionTestUtils.setField(prJob, "id", 102L);
        prJob.setProject(project);
        prJob.setJobType(JobType.PR_ANALYSIS);
        prJob.setBranchName("main");
        prJob.setPrNumber(42L);

        WebhookPayload payload = new WebhookPayload(
                EVcsProvider.GITHUB, "pull_request", "repo-id", "repo", "owner",
                "42", "feature/two", "main", "head-2", null);
        WebhookHandler.WebhookResult success = WebhookHandler.WebhookResult.ignored(
                "test dependency ordering");

        when(jobService.claimWebhookJobForExecution(any())).thenReturn(true);
        when(projectRepository.findById(1L)).thenReturn(Optional.of(project));
        when(branchAnalysisGateService.awaitDependencies(
                org.mockito.ArgumentMatchers.eq(1L),
                org.mockito.ArgumentMatchers.eq(prJob),
                any())).thenReturn(BranchAnalysisGateService.GateResult.READY);
        when(handler.handle(any(), any(), any())).thenReturn(success);

        processor.processWebhookInTransaction(
                EVcsProvider.GITHUB, 1L, payload, handler, prJob);

        var ordered = org.mockito.Mockito.inOrder(branchAnalysisGateService, handler);
        ordered.verify(branchAnalysisGateService).awaitDependencies(
                org.mockito.ArgumentMatchers.eq(1L),
                org.mockito.ArgumentMatchers.eq(prJob),
                any());
        ordered.verify(handler).handle(any(), any(), any());
    }

    @Test
    void promptDryRunSuppressesEveryWebhookVcsMutationPath() {
        System.setProperty(PromptDryRunMode.ENABLED_KEY, "true");
        System.setProperty(PromptDryRunMode.PROJECT_IDS_KEY, "12");
        when(project.getId()).thenReturn(12L);

        Job job = new Job();
        WebhookPayload payload = new WebhookPayload(
                EVcsProvider.BITBUCKET_CLOUD,
                "pullrequest:comment_created",
                "repo-id",
                "repository",
                "workspace",
                "41",
                "feature/review",
                "main",
                null,
                null);
        WebhookHandler.WebhookResult result = WebhookHandler.WebhookResult.success(
                "captured",
                Map.of("commandType", "analyze", "content", "must not publish"));

        ReflectionTestUtils.invokeMethod(
                processor, "postPlaceholderComment",
                EVcsProvider.BITBUCKET_CLOUD, project, payload, job);
        ReflectionTestUtils.invokeMethod(
                processor, "postResultToVcs",
                EVcsProvider.BITBUCKET_CLOUD, project, payload, result, null, job);
        ReflectionTestUtils.invokeMethod(
                processor, "postErrorToVcs",
                EVcsProvider.BITBUCKET_CLOUD, project, payload, "failure", null, job);
        ReflectionTestUtils.invokeMethod(
                processor, "postInfoToVcs",
                EVcsProvider.BITBUCKET_CLOUD, project, payload, "skipped", null, job);
        ReflectionTestUtils.invokeMethod(
                processor, "deletePlaceholderComment",
                EVcsProvider.BITBUCKET_CLOUD, project, payload, "comment-id");

        verifyNoInteractions(vcsServiceFactory);
    }
}
