package org.rostilos.codecrow.pipelineagent.qadoc;

import com.fasterxml.jackson.databind.ObjectMapper;
import org.rostilos.codecrow.core.model.job.*;
import org.rostilos.codecrow.core.persistence.repository.codeanalysis.CodeAnalysisRepository;
import org.rostilos.codecrow.core.persistence.repository.job.JobRepository;
import org.rostilos.codecrow.core.service.*;
import org.rostilos.codecrow.pipelineagent.generic.dto.webhook.WebhookPayload;
import org.rostilos.codecrow.pipelineagent.generic.processor.WebhookAsyncProcessor;
import org.rostilos.codecrow.pipelineagent.generic.processor.command.QaDocCommandProcessor;
import org.rostilos.codecrow.pipelineagent.generic.webhookhandler.WebhookHandlerFactory;
import org.slf4j.Logger;
import org.slf4j.LoggerFactory;
import org.springframework.beans.factory.annotation.Qualifier;
import org.springframework.data.domain.PageRequest;
import org.springframework.scheduling.annotation.Scheduled;
import org.springframework.stereotype.Component;
import java.time.OffsetDateTime;
import java.util.Map;
import java.util.Objects;
import java.util.concurrent.Executor;
import java.util.concurrent.RejectedExecutionException;

@Component
public class QaDocRecoveryScheduler {
    private static final Logger log=LoggerFactory.getLogger(QaDocRecoveryScheduler.class);
    private final CodeAnalysisRepository analyses;
    private final JobRepository repository;
    private final QaDocQueueService queue;
    private final JobService jobs;
    private final WebhookAsyncProcessor webhooks;
    private final WebhookHandlerFactory handlers;
    private final QaDocCommandProcessor command;
    private final PrChangeSummaryService summaries;
    private final ObjectMapper mapper;
    private final Executor executor;
    public QaDocRecoveryScheduler(CodeAnalysisRepository analyses, JobRepository repository, QaDocQueueService queue,
            JobService jobs, WebhookAsyncProcessor webhooks, WebhookHandlerFactory handlers,
            QaDocCommandProcessor command, PrChangeSummaryService summaries, ObjectMapper mapper,
            @Qualifier("pipelineActionExecutor") Executor executor) {
        this.analyses=analyses; this.repository=repository; this.queue=queue; this.jobs=jobs;
        this.webhooks=webhooks; this.handlers=handlers; this.command=command; this.summaries=summaries;
        this.mapper=mapper; this.executor=executor;
    }

    @Scheduled(fixedDelay=15000, initialDelay=15000)
    public void recover() {
        for (var analysis : analyses.findPendingQaDocumentation(PageRequest.of(0,50))) {
            try { queue.enqueue(analysis.getProject().getId(),analysis.getId()); }
            catch (Exception e) { log.warn("QA intent {} remains pending: {}",analysis.getId(),e.getMessage()); }
        }
        var now=OffsetDateTime.now();
        for (var job : repository.findRecoverableQaJobs(now.minusSeconds(30),now.minusMinutes(30),now.minusMinutes(1),PageRequest.of(0,50))) {
            try {
                if (!queue.prepareRetry(job.getId(),now)) continue;
                job.setStartedAt(null); job.setStatus(JobStatus.QUEUED);
                if (job.getTriggerSource() == JobTriggerSource.CHAINED) executor.execute(() -> executeAutomatic(job));
                else replayCommand(job);
            } catch (RejectedExecutionException full) {
                log.info("QA worker capacity full; job {} remains queued",job.getId());
            } catch (Exception e) {
                log.warn("QA dispatch {} will be retried: {}",job.getId(),e.getMessage());
            }
        }
    }

    private void replayCommand(Job job) {
        try {
            String saved=jobs.findWebhookDispatchPayload(job.getId()).orElseThrow(
                    () -> new IllegalStateException("No saved comment/actor payload; retry the QA command"));
            var payload=mapper.readValue(saved,WebhookPayload.class);
            var handler=handlers.getHandler(payload.provider(),payload).orElseThrow(
                    () -> new IllegalStateException("No handler for saved QA command"));
            // Replays the original actor through the normal authorization path.
            webhooks.processWebhookAsync(payload.provider(),job.getProject().getId(),payload,handler,job);
        } catch (RejectedExecutionException full) { throw full; }
        catch (Exception e) {
            if (jobs.claimQaJobForExecution(job)) jobs.failJob(job,"QA command recovery failed: " + e.getMessage());
        }
    }

    void executeAutomatic(Job job) {
        if (!jobs.claimQaJobForExecution(job)) return;
        try {
            var project=webhooks.loadAndInitializeProject(job.getProject().getId());
            var analysis=analyses.findByIdAndProjectId(job.getInputAnalysisId(),project.getId()).orElseThrow(
                    () -> new IllegalStateException("Source review no longer available; retry QA documentation explicitly"));
            if (!Objects.equals(job.getPrNumber(),analysis.getPrNumber()) || !Objects.equals(job.getCommitHash(),analysis.getCommitHash()))
                throw new IllegalStateException("Saved QA job does not match its source review");
            var repositoryInfo=project.getEffectiveVcsRepoInfo();
            var source=summaries.reviewSource(analysis);
            var raw=mapper.createObjectNode(); var pr=raw.putObject("pull_request");
            if (source.get("prTitle") instanceof String title) pr.put("title",title);
            if (source.get("prDescription") instanceof String description) pr.put("body",description);
            var payload=new WebhookPayload(repositoryInfo.getVcsConnection().getProviderType(),"qa_documentation",null,
                    repositoryInfo.getRepoSlug(),repositoryInfo.getRepoWorkspace(),String.valueOf(job.getPrNumber()),
                    analysis.getSourceBranchName(),analysis.getBranchName(),analysis.getCommitHash(),raw);
            var result=command.process(payload,project,event -> jobs.info(job,
                    String.valueOf(event.getOrDefault("state","qa")),String.valueOf(event.getOrDefault("message","Processing QA"))),
                    Map.of("analysisId",job.getInputAnalysisId()));
            if ("ignored".equals(result.status())) jobs.skipJob(job,result.message());
            else if (result.success()) jobs.completeJob(job);
            else jobs.failJob(job,result.message());
        } catch (Exception e) { jobs.failJob(job,"QA processing failed: " + e.getMessage()); }
    }
}
