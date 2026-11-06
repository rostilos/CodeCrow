package org.rostilos.codecrow.pipelineagent.generic.controller;

import com.fasterxml.jackson.databind.ObjectMapper;
import org.junit.jupiter.api.Test;
import org.junit.jupiter.params.ParameterizedTest;
import org.junit.jupiter.params.provider.ValueSource;
import org.rostilos.codecrow.analysisengine.dto.request.processor.PrProcessRequest;
import org.rostilos.codecrow.analysisengine.dto.request.processor.BranchProcessRequest;
import org.rostilos.codecrow.core.dto.project.ProjectDTO;
import org.rostilos.codecrow.core.model.codeanalysis.AnalysisType;
import org.rostilos.codecrow.core.model.job.Job;
import org.rostilos.codecrow.pipelineagent.generic.processor.PipelineActionProcessor;
import org.rostilos.codecrow.pipelineagent.generic.service.PipelineJobService;
import java.util.Map;
import java.util.concurrent.Executor;
import java.util.concurrent.RejectedExecutionException;
import static org.assertj.core.api.Assertions.*;
import static org.mockito.Mockito.*;

class PipelineAdmissionTest {
    final PipelineActionProcessor processor = mock(PipelineActionProcessor.class);
    final PipelineJobService jobs = mock(PipelineJobService.class);
    final ProjectDTO principal = mock(ProjectDTO.class);
    final Job job = mock(Job.class);

    ProviderPipelineActionController controller(boolean streaming, Executor executor) {
        when(principal.id()).thenReturn(10L);
        return new ProviderPipelineActionController(processor, jobs, new ObjectMapper(), streaming, executor);
    }
    PrProcessRequest pr() {
        var request = new PrProcessRequest();
        request.projectId = 10L; request.pullRequestId = 2L; request.commitHash = "exact-revision";
        request.sourceBranchName = "feature"; request.targetBranchName = "main";
        request.analysisType = AnalysisType.PR_REVIEW;
        return request;
    }
    BranchProcessRequest branch() {
        var request = new BranchProcessRequest();
        request.projectId = 10L; request.commitHash = "branch-revision";
        request.targetBranchName = "main"; request.analysisType = AnalysisType.BRANCH_ANALYSIS;
        return request;
    }
    @Test void deniedPrBranchAndMissingPrincipalNeverMutateJobs() throws Exception {
        var controller = controller(true, Runnable::run);
        var pr = pr(); pr.projectId = 99L;
        var branch = branch(); branch.projectId = 99L;
        assertThatThrownBy(() -> controller.handlePrWebhook(principal, pr)).hasMessage("token_project_mismatch");
        assertThatThrownBy(() -> controller.handleBranchWebhook(principal, branch)).hasMessage("token_project_mismatch");
        assertThatThrownBy(() -> controller.handlePrWebhook(null, pr())).hasMessage("token_project_mismatch");
        verifyNoInteractions(jobs, processor);
    }
    @ParameterizedTest @ValueSource(booleans = {true, false})
    void prAndBranchPersistBeforeDispatchAndActuallyExecute(boolean streaming) throws Exception {
        var controller = controller(streaming, Runnable::run);
        when(jobs.createPipelinePrJob(any())).thenReturn(job);
        when(jobs.createPipelineBranchJob(any())).thenReturn(job);
        when(jobs.claimForExecution(job)).thenReturn(true);
        when(job.getExternalId()).thenReturn("durable-job");
        when(jobs.createDualConsumer(eq(job), any())).thenAnswer(call -> call.getArgument(1));
        when(processor.processPipelineActionWithConsumer(any(), any(), eq(job))).thenReturn(Map.of("status", "complete"));
        var pr = pr(); var branch = branch();
        assertThat(controller.handlePrWebhook(principal, pr).getStatusCode().value()).isEqualTo(streaming ? 200 : 202);
        assertThat(controller.handleBranchWebhook(principal, branch).getStatusCode().value()).isEqualTo(streaming ? 200 : 202);
        var ordered = inOrder(jobs, processor);
        ordered.verify(jobs).createPipelinePrJob(pr);
        ordered.verify(jobs).persistDispatch(job, pr);
        ordered.verify(jobs).claimForExecution(job);
        ordered.verify(jobs).createDualConsumer(eq(job), any());
        ordered.verify(processor).processPipelineActionWithConsumer(eq(pr), any(), eq(job));
        ordered.verify(jobs).completeJob(eq(job), any());
        ordered.verify(jobs).createPipelineBranchJob(branch);
        ordered.verify(jobs).persistDispatch(job, branch);
        ordered.verify(jobs).claimForExecution(job);
        ordered.verify(jobs).createDualConsumer(eq(job), any());
        ordered.verify(processor).processPipelineActionWithConsumer(eq(branch), any(), eq(job));
        ordered.verify(jobs).completeJob(eq(job), any());
    }
    @Test void rejectedDispatchRemainsDurableAndReturnsItsJobId() throws Exception {
        var controller = controller(true, task -> { throw new RejectedExecutionException("busy"); });
        when(jobs.createPipelinePrJob(any())).thenReturn(job);
        when(job.getExternalId()).thenReturn("durable-job");
        var request = pr();
        var response = controller.handlePrWebhook(principal, request);
        assertThat(response.getStatusCode().value()).isEqualTo(202);
        assertThat(response.getBody()).isEqualTo(Map.of("status", "accepted", "jobId", "durable-job"));
        verify(jobs).persistDispatch(job, request);
        verify(jobs, never()).failJob(any(), any());
        verifyNoInteractions(processor);
    }
}
