package org.rostilos.codecrow.pipelineagent.generic.service;

import com.fasterxml.jackson.databind.ObjectMapper;
import org.junit.jupiter.api.Test;
import org.junit.jupiter.params.ParameterizedTest;
import org.junit.jupiter.params.provider.ValueSource;
import org.rostilos.codecrow.analysisengine.dto.request.processor.*;
import org.rostilos.codecrow.core.model.job.*;
import org.rostilos.codecrow.core.model.project.Project;
import org.rostilos.codecrow.core.service.JobService;
import org.rostilos.codecrow.pipelineagent.generic.processor.*;
import org.rostilos.codecrow.pipelineagent.generic.webhookhandler.WebhookHandlerFactory;
import java.util.*;
import java.util.concurrent.Executor;
import java.util.concurrent.RejectedExecutionException;
import static org.assertj.core.api.Assertions.*;
import static org.mockito.Mockito.*;
import org.mockito.ArgumentCaptor;

class PipelineRecoveryTest {
    final JobService jobs = mock(JobService.class);
    final PipelineJobService pipelineJobs = mock(PipelineJobService.class);
    final PipelineActionProcessor processor = mock(PipelineActionProcessor.class);
    final Job job = mock(Job.class);
    final Project project = mock(Project.class);
    final ObjectMapper mapper = new ObjectMapper();
    WebhookJobRecoveryScheduler scheduler(Executor executor, boolean branch, boolean abandoned) {
        when(job.getId()).thenReturn(1L); when(job.getProject()).thenReturn(project);
        when(project.getId()).thenReturn(20L); when(job.getTriggerSource()).thenReturn(JobTriggerSource.PIPELINE);
        when(job.getJobType()).thenReturn(branch ? JobType.BRANCH_ANALYSIS : JobType.PR_ANALYSIS);
        if (abandoned) {
            when(jobs.findAbandonedRunningWebhookJobs(any(), eq(20))).thenReturn(List.of(job));
            when(jobs.claimAbandonedRunningWebhookJob(eq(1L), any())).thenReturn(true);
        } else {
            when(jobs.findRecoverableWebhookJobs(any(), eq(50))).thenReturn(List.of(job));
            when(jobs.claimRecoverableWebhookJob(eq(1L), any())).thenReturn(true);
        }
        return new WebhookJobRecoveryScheduler(jobs, mapper, mock(WebhookHandlerFactory.class),
                mock(WebhookAsyncProcessor.class), pipelineJobs, processor, executor);
    }
    String payload(boolean branch) {
        return branch ? "{\"projectId\":20,\"targetBranchName\":\"main\",\"commitHash\":\"original-head\",\"analysisType\":\"BRANCH_ANALYSIS\",\"sourcePrNumber\":17}"
                : "{\"projectId\":20,\"pullRequestId\":17,\"targetBranchName\":\"main\",\"sourceBranchName\":\"feature\",\"commitHash\":\"original-head\",\"analysisType\":\"PR_REVIEW\",\"preAcquiredLockKey\":\"dead-process-lock\",\"prTitle\":\"original title\"}";
    }
    @ParameterizedTest @ValueSource(booleans={true,false})
    void restartRecoversCompleteTypedInputAndNeverReusesOldLock(boolean branch) throws Exception {
        var scheduler = scheduler(Runnable::run, branch, false);
        when(jobs.findWebhookDispatchPayload(1L)).thenReturn(Optional.of(payload(branch)));
        when(pipelineJobs.claimForExecution(job)).thenReturn(true);
        when(processor.processPipelineActionWithConsumer(any(), any(), eq(job))).thenReturn(Map.of("status", "complete"));
        scheduler.recoverAcceptedJobs();
        var request = ArgumentCaptor.forClass(AnalysisProcessRequest.class);
        verify(processor).processPipelineActionWithConsumer(request.capture(), any(), eq(job));
        assertThat(request.getValue().getCommitHash()).isEqualTo("original-head");
        if (request.getValue() instanceof PrProcessRequest pr) {
            assertThat(pr.getPreAcquiredLockKey()).isNull(); assertThat(pr.getPrTitle()).isEqualTo("original title");
        } else assertThat(((BranchProcessRequest)request.getValue()).getSourcePrNumber()).isEqualTo(17L);
        verify(pipelineJobs).completeJob(eq(job), any());
    }
    @Test void abandonedRunningJobIsClaimedAndResumed() throws Exception {
        var scheduler = scheduler(Runnable::run, false, true);
        when(jobs.findWebhookDispatchPayload(1L)).thenReturn(Optional.of(payload(false)));
        when(pipelineJobs.claimForExecution(job)).thenReturn(true);
        when(processor.processPipelineActionWithConsumer(any(), any(), eq(job))).thenReturn(Map.of("status", "complete"));
        scheduler.recoverAcceptedJobs();
        verify(processor).processPipelineActionWithConsumer(any(), any(), eq(job));
    }
    @Test void missingLegacyPayloadAndCrossProjectPayloadTerminateWithDiagnostic() throws Exception {
        var scheduler = scheduler(Runnable::run, false, false);
        when(jobs.findWebhookDispatchPayload(1L)).thenReturn(Optional.empty());
        scheduler.recoverAcceptedJobs();
        verify(jobs).failJob(eq(job), contains("no durable request"));
        when(jobs.findWebhookDispatchPayload(1L)).thenReturn(Optional.of(payload(false).replace("20", "99")));
        scheduler.recoverAcceptedJobs();
        verify(jobs).failJob(eq(job), contains("does not belong"));
        verifyNoInteractions(processor);
    }
    @Test void saturationKeepsPersistedWorkAndLostExecutionClaimDoesNotDuplicate() throws Exception {
        var saturated = scheduler(task -> { throw new RejectedExecutionException("busy"); }, false, false);
        when(jobs.findWebhookDispatchPayload(1L)).thenReturn(Optional.of(payload(false)));
        saturated.recoverAcceptedJobs();
        verify(jobs, never()).failJob(any(), any());
        var scheduler = scheduler(Runnable::run, false, false);
        when(pipelineJobs.claimForExecution(job)).thenReturn(false);
        scheduler.recoverAcceptedJobs();
        verifyNoInteractions(processor);
    }
}
