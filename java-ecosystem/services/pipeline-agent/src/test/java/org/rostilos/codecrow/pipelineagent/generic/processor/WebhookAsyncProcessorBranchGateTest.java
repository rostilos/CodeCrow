package org.rostilos.codecrow.pipelineagent.generic.processor;

import org.junit.jupiter.api.BeforeEach;
import org.junit.jupiter.api.Test;
import org.junit.jupiter.api.extension.ExtendWith;
import org.mockito.Mock;
import org.mockito.junit.jupiter.MockitoExtension;
import org.rostilos.codecrow.analysisengine.service.branch.BranchAnalysisGateService;
import org.rostilos.codecrow.analysisengine.service.vcs.VcsServiceFactory;
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

import static org.mockito.ArgumentMatchers.any;
import static org.mockito.Mockito.never;
import static org.mockito.Mockito.verify;
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
        processor = new WebhookAsyncProcessor(
                projectRepository, jobService, vcsServiceFactory,
                branchAnalysisGateService, ragOperationsService);
        ReflectionTestUtils.setField(processor, "self", processor);
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

        when(projectRepository.findById(1L)).thenReturn(Optional.of(project));
        when(branchAnalysisGateService.awaitTurn(
                org.mockito.ArgumentMatchers.eq(1L),
                org.mockito.ArgumentMatchers.eq("main"),
                org.mockito.ArgumentMatchers.eq(101L),
                org.mockito.ArgumentMatchers.eq(41L),
                any()))
                .thenReturn(BranchAnalysisGateService.GateResult.SUPERSEDED);

        processor.processWebhookInTransaction(
                EVcsProvider.GITHUB, 1L, payload, handler, branchJob);

        verify(jobService).startJob(branchJob);
        verify(jobService).skipJob(
                org.mockito.ArgumentMatchers.eq(branchJob),
                org.mockito.ArgumentMatchers.contains("Superseded"));
        verify(handler, never()).handle(any(), any(), any());
        verify(jobService, never()).completeJob(any(Job.class));
        verify(ragOperationsService).deletePrFiles(project, 41);
    }
}
