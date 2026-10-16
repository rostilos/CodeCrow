package org.rostilos.codecrow.pipelineagent.generic.service;

import com.fasterxml.jackson.databind.ObjectMapper;
import com.fasterxml.jackson.databind.node.ObjectNode;
import org.rostilos.codecrow.core.model.job.Job;
import org.rostilos.codecrow.core.model.job.JobType;
import org.rostilos.codecrow.core.model.job.JobTriggerSource;
import org.rostilos.codecrow.analysisengine.dto.request.processor.AnalysisProcessRequest;
import org.rostilos.codecrow.analysisengine.dto.request.processor.PrProcessRequest;
import org.rostilos.codecrow.analysisengine.dto.request.processor.BranchProcessRequest;
import org.rostilos.codecrow.pipelineagent.generic.processor.PipelineActionProcessor;
import org.springframework.beans.factory.annotation.Qualifier;
import java.util.concurrent.Executor;
import java.util.Map;
import org.rostilos.codecrow.core.model.project.Project;
import org.rostilos.codecrow.core.model.vcs.EVcsProvider;
import org.rostilos.codecrow.core.model.vcs.VcsRepoInfo;
import org.rostilos.codecrow.core.service.JobService;
import org.rostilos.codecrow.pipelineagent.generic.dto.webhook.WebhookPayload;
import org.rostilos.codecrow.pipelineagent.generic.processor.WebhookAsyncProcessor;
import org.rostilos.codecrow.pipelineagent.generic.webhookhandler.WebhookHandler;
import org.rostilos.codecrow.pipelineagent.generic.webhookhandler.WebhookHandlerFactory;
import org.slf4j.Logger;
import org.slf4j.LoggerFactory;
import org.springframework.scheduling.annotation.Scheduled;
import org.springframework.stereotype.Service;

import java.time.OffsetDateTime;
import java.util.List;
import java.util.Optional;

/**
 * Replays accepted webhook jobs whose in-process dispatch was interrupted.
 */
@Service
public class WebhookJobRecoveryScheduler {
    private static final Logger log =
            LoggerFactory.getLogger(WebhookJobRecoveryScheduler.class);

    private final JobService jobService;
    private final ObjectMapper objectMapper;
    private final WebhookHandlerFactory webhookHandlerFactory;
    private final WebhookAsyncProcessor webhookAsyncProcessor;
    private final PipelineJobService pipelineJobService;
    private final PipelineActionProcessor pipelineActionProcessor;
    private final Executor actionExecutor;

    public WebhookJobRecoveryScheduler(
            JobService jobService,
            ObjectMapper objectMapper,
            WebhookHandlerFactory webhookHandlerFactory,
            WebhookAsyncProcessor webhookAsyncProcessor,
            PipelineJobService pipelineJobService,
            PipelineActionProcessor pipelineActionProcessor,
            @Qualifier("pipelineActionExecutor") Executor actionExecutor) {
        this.jobService = jobService;
        this.objectMapper = objectMapper;
        this.webhookHandlerFactory = webhookHandlerFactory;
        this.webhookAsyncProcessor = webhookAsyncProcessor;
        this.pipelineJobService = pipelineJobService;
        this.pipelineActionProcessor = pipelineActionProcessor;
        this.actionExecutor = actionExecutor;
    }

    @Scheduled(
            fixedDelayString = "${webhook.job.recovery.interval.ms:15000}",
            initialDelayString = "${webhook.job.recovery.initial-delay.ms:15000}")
    public void recoverAcceptedJobs() {
        OffsetDateTime pendingThreshold = OffsetDateTime.now().minusSeconds(30);
        List<Job> candidates = jobService.findRecoverableWebhookJobs(pendingThreshold, 50);
        for (Job candidate : candidates) {
            if (!isSupportedWebhookAnalysis(candidate)) {
                continue;
            }
            if (!jobService.claimRecoverableWebhookJob(
                    candidate.getId(), pendingThreshold)) {
                continue;
            }
            recover(candidate);
        }

        OffsetDateTime abandonedThreshold = OffsetDateTime.now().minusMinutes(30);
        List<Job> abandoned = jobService.findAbandonedRunningWebhookJobs(
                abandonedThreshold, 20);
        for (Job candidate : abandoned) {
            if (!isSupportedWebhookAnalysis(candidate)) {
                continue;
            }
            if (!jobService.claimAbandonedRunningWebhookJob(
                    candidate.getId(), abandonedThreshold)) {
                continue;
            }
            jobService.warn(candidate, "recovery",
                    "No pipeline activity for 30 minutes; resuming the persisted job");
            recover(candidate);
        }
    }

    private static boolean isSupportedWebhookAnalysis(Job job) {
        return job != null && (job.getJobType() == JobType.PR_ANALYSIS
                || job.getJobType() == JobType.BRANCH_ANALYSIS);
    }

    private void recover(Job job) {
        try {
            Optional<String> persistedPayload =
                    jobService.findWebhookDispatchPayload(job.getId());
            if (job.getTriggerSource() == JobTriggerSource.PIPELINE) {
                recoverPipeline(job, persistedPayload);
                return;
            }
            WebhookPayload payload = persistedPayload.isPresent()
                    ? objectMapper.readValue(persistedPayload.get(), WebhookPayload.class)
                    : recoverLegacyAnalysisPayload(job);
            if (payload == null) {
                jobService.failJob(job,
                        "This older pending webhook job does not contain enough input to "
                                + "resume safely. Retry the analysis.");
                return;
            }
            Optional<WebhookHandler> handler =
                    webhookHandlerFactory.getHandler(payload.provider(), payload);
            if (handler.isEmpty()) {
                jobService.failJob(job,
                        "Accepted webhook work cannot be recovered because no handler supports "
                                + payload.provider() + " event " + payload.eventType());
                return;
            }

            jobService.info(job, "requeued",
                    "Pipeline-agent restart or lost dispatch detected; resuming persisted webhook work");
            webhookAsyncProcessor.processWebhookAsync(
                    payload.provider(),
                    job.getProject().getId(),
                    payload,
                    handler.get(),
                    job);
        } catch (java.util.concurrent.RejectedExecutionException saturated) {
            log.info("Webhook recovery capacity is full; job {} remains durably queued",
                    job.getExternalId());
        } catch (Exception recoveryError) {
            log.error("Failed to recover webhook job {}: {}",
                    job.getExternalId(), recoveryError.getMessage(), recoveryError);
            jobService.failJob(job,
                    "Failed to recover accepted webhook work: " + recoveryError.getMessage());
        }
    }

    private void recoverPipeline(Job job, Optional<String> persistedPayload) throws Exception {
        if (persistedPayload.isEmpty()) {
            jobService.failJob(job, "Older pipeline job has no durable request payload; retry the analysis");
            return;
        }
        AnalysisProcessRequest request = job.getJobType() == JobType.PR_ANALYSIS
                ? objectMapper.readValue(persistedPayload.get(), PrProcessRequest.class)
                : objectMapper.readValue(persistedPayload.get(), BranchProcessRequest.class);
        if (!job.getProject().getId().equals(request.getProjectId())) {
            jobService.failJob(job, "Persisted pipeline request does not belong to this job's project");
            return;
        }
        // Locks are process-owned and must never survive a restart in a replayed request.
        if (request instanceof PrProcessRequest pr) {
            pr.preAcquiredLockKey = null;
        }
        jobService.info(job, "requeued", "Resuming durably persisted pipeline work");
        actionExecutor.execute(() -> {
            if (!pipelineJobService.claimForExecution(job)) {
                return;
            }
            try {
                Map<String, Object> result = pipelineActionProcessor.processPipelineActionWithConsumer(
                        request, pipelineJobService.createDualConsumer(job, null), job);
                if ("locked".equals(result.get("status"))) {
                    pipelineJobService.failJob(job, "Analysis lock timeout: " + result.get("message"));
                } else {
                    pipelineJobService.completeJob(job, result);
                }
            } catch (Exception error) {
                pipelineJobService.failJob(job, "Recovered pipeline processing failed: " + error.getMessage());
            }
        });
    }

    private WebhookPayload recoverLegacyAnalysisPayload(Job job) {
        if (job.getJobType() != JobType.BRANCH_ANALYSIS
                && job.getJobType() != JobType.PR_ANALYSIS) {
            return null;
        }

        Project project = webhookAsyncProcessor.loadAndInitializeProject(
                job.getProject().getId());
        VcsRepoInfo repository = project.getEffectiveVcsRepoInfo();
        if (repository == null || repository.getVcsConnection() == null) {
            return null;
        }
        EVcsProvider provider = repository.getVcsConnection().getProviderType();
        boolean pullRequest = job.getJobType() == JobType.PR_ANALYSIS;
        String eventType = eventType(provider, pullRequest);
        ObjectNode rawPayload = objectMapper.createObjectNode();
        if (pullRequest && provider == EVcsProvider.GITHUB) {
            rawPayload.put("action", "synchronize");
            rawPayload.putObject("pull_request");
        } else if (pullRequest && provider == EVcsProvider.GITLAB) {
            ObjectNode attributes = rawPayload.putObject("object_attributes");
            attributes.put("action", "update");
            attributes.put("state", "opened");
            attributes.put("iid", job.getPrNumber());
        } else if (pullRequest && provider == EVcsProvider.BITBUCKET_CLOUD) {
            rawPayload.putObject("pullrequest");
        }

        String branch = job.getBranchName();
        return new WebhookPayload(
                provider,
                eventType,
                null,
                repository.getRepoSlug(),
                repository.getRepoWorkspace(),
                pullRequest && job.getPrNumber() != null
                        ? String.valueOf(job.getPrNumber()) : null,
                branch,
                branch,
                job.getCommitHash(),
                rawPayload);
    }

    private String eventType(EVcsProvider provider, boolean pullRequest) {
        if (pullRequest) {
            return switch (provider) {
                case GITHUB -> "pull_request";
                case GITLAB -> "merge_request";
                case BITBUCKET_CLOUD -> "pullrequest:updated";
                case BITBUCKET_SERVER -> "pullrequest:updated";
            };
        }
        return switch (provider) {
            case GITHUB, GITLAB -> "push";
            case BITBUCKET_CLOUD, BITBUCKET_SERVER -> "repo:push";
        };
    }
}
