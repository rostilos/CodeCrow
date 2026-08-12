package org.rostilos.codecrow.pipelineagent.generic.service;

import static org.assertj.core.api.Assertions.assertThat;
import static org.mockito.ArgumentMatchers.any;
import static org.mockito.ArgumentMatchers.eq;
import static org.mockito.Mockito.mock;
import static org.mockito.Mockito.never;
import static org.mockito.Mockito.verify;
import static org.mockito.Mockito.when;

import com.fasterxml.jackson.databind.ObjectMapper;
import com.fasterxml.jackson.databind.node.JsonNodeFactory;
import java.util.List;
import java.util.Optional;
import org.junit.jupiter.api.Test;
import org.mockito.ArgumentCaptor;
import org.rostilos.codecrow.core.model.job.Job;
import org.rostilos.codecrow.core.model.job.JobType;
import org.rostilos.codecrow.core.model.project.Project;
import org.rostilos.codecrow.core.model.vcs.EVcsProvider;
import org.rostilos.codecrow.core.model.vcs.VcsConnection;
import org.rostilos.codecrow.core.model.vcs.VcsRepoInfo;
import org.rostilos.codecrow.core.service.JobService;
import org.rostilos.codecrow.pipelineagent.generic.dto.webhook.WebhookPayload;
import org.rostilos.codecrow.pipelineagent.generic.processor.WebhookAsyncProcessor;
import org.rostilos.codecrow.pipelineagent.generic.webhookhandler.WebhookHandler;
import org.rostilos.codecrow.pipelineagent.generic.webhookhandler.WebhookHandlerFactory;

class WebhookJobRecoverySchedulerTest {

    private final JobService jobService = mock(JobService.class);
    private final ObjectMapper objectMapper = new ObjectMapper();
    private final WebhookHandlerFactory handlerFactory = mock(WebhookHandlerFactory.class);
    private final WebhookAsyncProcessor asyncProcessor = mock(WebhookAsyncProcessor.class);
    private final WebhookJobRecoveryScheduler scheduler = new WebhookJobRecoveryScheduler(
            jobService, objectMapper, handlerFactory, asyncProcessor);

    @Test
    void replaysPersistedWebhookPayloadAfterLostDispatch() throws Exception {
        Project project = mock(Project.class);
        Job job = mock(Job.class);
        WebhookHandler handler = mock(WebhookHandler.class);
        WebhookPayload payload = new WebhookPayload(
                EVcsProvider.GITHUB, "push", null, "repo", "owner",
                null, "main", null, "abc123", JsonNodeFactory.instance.objectNode());

        when(job.getId()).thenReturn(10L);
        when(job.getExternalId()).thenReturn("job-10");
        when(job.getJobType()).thenReturn(JobType.BRANCH_ANALYSIS);
        when(job.getProject()).thenReturn(project);
        when(project.getId()).thenReturn(20L);
        when(jobService.findWebhookDispatchPayload(10L))
                .thenReturn(Optional.of(objectMapper.writeValueAsString(payload)));
        when(jobService.findRecoverableWebhookJobs(any(), eq(50))).thenReturn(List.of(job));
        when(jobService.claimRecoverableWebhookJob(eq(10L), any())).thenReturn(true);
        when(handlerFactory.getHandler(EVcsProvider.GITHUB, payload))
                .thenReturn(Optional.of(handler));

        scheduler.recoverAcceptedJobs();

        verify(asyncProcessor).processWebhookAsync(
                EVcsProvider.GITHUB, 20L, payload, handler, job);
    }

    @Test
    void restoresLegacyPendingBranchJobWithoutStoredPayload() {
        Project project = mock(Project.class);
        VcsRepoInfo repository = mock(VcsRepoInfo.class);
        VcsConnection connection = mock(VcsConnection.class);
        Job job = mock(Job.class);
        WebhookHandler handler = mock(WebhookHandler.class);

        when(job.getId()).thenReturn(11L);
        when(job.getExternalId()).thenReturn("job-11");
        when(job.getJobType()).thenReturn(JobType.BRANCH_ANALYSIS);
        when(job.getBranchName()).thenReturn("release/10x");
        when(job.getCommitHash()).thenReturn("abc123");
        when(job.getProject()).thenReturn(project);
        when(project.getId()).thenReturn(21L);
        when(project.getEffectiveVcsRepoInfo()).thenReturn(repository);
        when(repository.getVcsConnection()).thenReturn(connection);
        when(repository.getRepoWorkspace()).thenReturn("owner");
        when(repository.getRepoSlug()).thenReturn("repo");
        when(connection.getProviderType()).thenReturn(EVcsProvider.GITHUB);
        when(asyncProcessor.loadAndInitializeProject(21L)).thenReturn(project);
        when(jobService.findRecoverableWebhookJobs(any(), eq(50))).thenReturn(List.of(job));
        when(jobService.claimRecoverableWebhookJob(eq(11L), any())).thenReturn(true);
        when(handlerFactory.getHandler(eq(EVcsProvider.GITHUB), any(WebhookPayload.class)))
                .thenReturn(Optional.of(handler));

        scheduler.recoverAcceptedJobs();

        ArgumentCaptor<WebhookPayload> payload = ArgumentCaptor.forClass(WebhookPayload.class);
        verify(asyncProcessor).processWebhookAsync(
                eq(EVcsProvider.GITHUB), eq(21L), payload.capture(), eq(handler), eq(job));
        assertThat(payload.getValue().eventType()).isEqualTo("push");
        assertThat(payload.getValue().sourceBranch()).isEqualTo("release/10x");
        assertThat(payload.getValue().commitHash()).isEqualTo("abc123");
    }

    @Test
    void neverClaimsPendingRagChildrenOrConsultsPersistedPayload() {
        Job initial = mock(Job.class);
        Job incremental = mock(Job.class);
        when(initial.getId()).thenReturn(12L);
        when(initial.getJobType()).thenReturn(JobType.RAG_INITIAL_INDEX);
        when(incremental.getId()).thenReturn(13L);
        when(incremental.getJobType()).thenReturn(JobType.RAG_INCREMENTAL_INDEX);
        when(jobService.findRecoverableWebhookJobs(any(), eq(50)))
                .thenReturn(List.of(initial, incremental));
        scheduler.recoverAcceptedJobs();

        verify(jobService, never()).claimRecoverableWebhookJob(eq(12L), any());
        verify(jobService, never()).claimRecoverableWebhookJob(eq(13L), any());
        verify(jobService, never()).findWebhookDispatchPayload(12L);
        verify(jobService, never()).findWebhookDispatchPayload(13L);
        verify(asyncProcessor, never()).processWebhookAsync(
                any(), any(), any(), any(), any());
    }

    @Test
    void neverClaimsAbandonedRunningRagChildren() {
        Job initial = mock(Job.class);
        Job incremental = mock(Job.class);
        when(initial.getId()).thenReturn(14L);
        when(initial.getJobType()).thenReturn(JobType.RAG_INITIAL_INDEX);
        when(incremental.getId()).thenReturn(15L);
        when(incremental.getJobType()).thenReturn(JobType.RAG_INCREMENTAL_INDEX);
        when(jobService.findAbandonedRunningWebhookJobs(any(), eq(20)))
                .thenReturn(List.of(initial, incremental));

        scheduler.recoverAcceptedJobs();

        verify(jobService, never()).claimAbandonedRunningWebhookJob(eq(14L), any());
        verify(jobService, never()).claimAbandonedRunningWebhookJob(eq(15L), any());
        verify(jobService, never()).findWebhookDispatchPayload(14L);
        verify(jobService, never()).findWebhookDispatchPayload(15L);
        verify(asyncProcessor, never()).processWebhookAsync(
                any(), any(), any(), any(), any());
    }
}
