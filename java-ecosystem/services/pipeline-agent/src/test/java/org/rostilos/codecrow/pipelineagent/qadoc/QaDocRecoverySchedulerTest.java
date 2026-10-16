package org.rostilos.codecrow.pipelineagent.qadoc;

import com.fasterxml.jackson.databind.ObjectMapper;
import org.junit.jupiter.api.Test;
import org.junit.jupiter.params.ParameterizedTest;
import org.junit.jupiter.params.provider.EnumSource;
import org.rostilos.codecrow.core.model.codeanalysis.CodeAnalysis;
import org.rostilos.codecrow.core.model.job.*;
import org.rostilos.codecrow.core.model.project.Project;
import org.rostilos.codecrow.core.model.vcs.EVcsProvider;
import org.rostilos.codecrow.core.persistence.repository.codeanalysis.CodeAnalysisRepository;
import org.rostilos.codecrow.core.persistence.repository.job.JobRepository;
import org.rostilos.codecrow.core.service.*;
import org.rostilos.codecrow.pipelineagent.generic.dto.webhook.WebhookPayload;
import org.rostilos.codecrow.pipelineagent.generic.processor.WebhookAsyncProcessor;
import org.rostilos.codecrow.pipelineagent.generic.processor.command.QaDocCommandProcessor;
import org.rostilos.codecrow.pipelineagent.generic.webhookhandler.*;
import org.springframework.test.util.ReflectionTestUtils;
import java.util.*;
import java.util.concurrent.*;
import static org.assertj.core.api.Assertions.*;
import static org.mockito.Mockito.*;

class QaDocRecoverySchedulerTest {
    final CodeAnalysisRepository analyses=mock(CodeAnalysisRepository.class);
    final JobRepository repository=mock(JobRepository.class);
    final QaDocQueueService queue=mock(QaDocQueueService.class);
    final JobService jobs=mock(JobService.class);
    final WebhookAsyncProcessor webhooks=mock(WebhookAsyncProcessor.class);
    final WebhookHandlerFactory handlers=mock(WebhookHandlerFactory.class);
    final QaDocCommandProcessor command=mock(QaDocCommandProcessor.class);
    final PrChangeSummaryService summaries=mock(PrChangeSummaryService.class);
    final ObjectMapper mapper=new ObjectMapper();
    QaDocRecoveryScheduler scheduler(Executor executor) {
        return new QaDocRecoveryScheduler(analyses,repository,queue,jobs,webhooks,handlers,command,summaries,mapper,executor);
    }
    Job candidate(JobTriggerSource trigger) {
        var project=new Project(); ReflectionTestUtils.setField(project,"id",1L);
        var job=new Job(); ReflectionTestUtils.setField(job,"id",2L);
        job.setProject(project); job.setJobType(JobType.QA_DOC_COMMAND); job.setTriggerSource(trigger);
        job.setPrNumber(7L); job.setStatus(JobStatus.QUEUED); job.setInputAnalysisId(3L); job.setCommitHash("head");
        when(repository.findRecoverableQaJobs(any(),any(),any(),any())).thenReturn(List.of(job));
        when(queue.prepareRetry(eq(2L),any())).thenReturn(true);
        return job;
    }
    @Test void saturationLeavesDurableWorkUnclaimed() {
        candidate(JobTriggerSource.CHAINED);
        scheduler(work -> { throw new RejectedExecutionException("full"); }).recover();
        verify(jobs,never()).claimQaJobForExecution(any()); verify(jobs,never()).failJob(any(),anyString());
    }
    @Test void missingCompletionEventQueuesPersistedIntent() {
        var analysis=new CodeAnalysis(); var project=new Project(); ReflectionTestUtils.setField(project,"id",1L);
        analysis.setProject(project); ReflectionTestUtils.setField(analysis,"id",3L);
        when(analyses.findPendingQaDocumentation(any())).thenReturn(List.of(analysis));
        scheduler(Runnable::run).recover(); verify(queue).enqueue(1L,3L);
    }
    @ParameterizedTest @EnumSource(EVcsProvider.class)
    void manualRecoveryReplaysSavedActorThroughAuthorizationHandler(EVcsProvider provider) throws Exception {
        var job=candidate(JobTriggerSource.WEBHOOK);
        var actor=new WebhookPayload.CommentData("comment","/codecrow qa-doc","actor-id","actor",null,false,null,null);
        var payload=new WebhookPayload(provider,"comment","repo-id","repo","workspace","7","feature/TASK-1","main","head",mapper.createObjectNode(),actor,null,null);
        when(jobs.findWebhookDispatchPayload(2L)).thenReturn(Optional.of(mapper.writeValueAsString(payload)));
        var handler=mock(WebhookHandler.class); when(handlers.getHandler(eq(provider),any(WebhookPayload.class))).thenReturn(Optional.of(handler));
        scheduler(Runnable::run).recover();
        verify(webhooks).processWebhookAsync(eq(provider),eq(1L),eq(payload),eq(handler),eq(job));
        verifyNoInteractions(command);
    }
    @Test void autoWorkerUsesSavedSourceAndExactAnalysisThenLeavesFailureRetryable() {
        var job=candidate(JobTriggerSource.CHAINED); var project=job.getProject();
        var repo=mock(org.rostilos.codecrow.core.model.vcs.VcsRepoInfo.class);
        var conn=mock(org.rostilos.codecrow.core.model.vcs.VcsConnection.class);
        var initialized=spy(project); doReturn(repo).when(initialized).getEffectiveVcsRepoInfo();
        when(repo.getVcsConnection()).thenReturn(conn); when(conn.getProviderType()).thenReturn(EVcsProvider.GITLAB);
        when(jobs.claimQaJobForExecution(job)).thenReturn(true); when(webhooks.loadAndInitializeProject(1L)).thenReturn(initialized);
        var analysis=new CodeAnalysis(); analysis.setProject(project); analysis.setPrNumber(7L); analysis.setCommitHash("head");
        analysis.setSourceBranchName("feature/TASK-1"); analysis.setBranchName("main");
        when(analyses.findByIdAndProjectId(3L,1L)).thenReturn(Optional.of(analysis));
        when(summaries.reviewSource(analysis)).thenReturn(Map.of("prTitle","saved title","prDescription","saved requirements"));
        when(command.process(any(),eq(initialized),any(),eq(Map.of("analysisId",3L)))).thenAnswer(inv -> {
            WebhookPayload payload=inv.getArgument(0);
            assertThat(payload.commitHash()).isEqualTo("head");
            assertThat(payload.rawPayload().path("pull_request").path("title").asText()).isEqualTo("saved title");
            return WebhookHandler.WebhookResult.error("handoff failed");
        });
        scheduler(Runnable::run).recover(); verify(jobs).failJob(job,"handoff failed");
        verify(jobs,never()).completeJob(any());
    }
}
