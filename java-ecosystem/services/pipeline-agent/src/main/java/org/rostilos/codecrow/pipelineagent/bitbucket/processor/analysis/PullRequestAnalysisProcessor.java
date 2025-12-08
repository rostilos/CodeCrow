package org.rostilos.codecrow.pipelineagent.bitbucket.processor.analysis;

import org.rostilos.codecrow.core.model.analysis.AnalysisLockType;
import org.rostilos.codecrow.core.model.codeanalysis.CodeAnalysis;
import org.rostilos.codecrow.core.model.project.Project;
import org.rostilos.codecrow.core.model.pullrequest.PullRequest;
import org.rostilos.codecrow.core.service.CodeAnalysisService;
import org.rostilos.codecrow.pipelineagent.generic.dto.request.processor.PrProcessRequest;
import org.rostilos.codecrow.pipelineagent.bitbucket.processor.BitbucketWebhookProcessor;
import org.rostilos.codecrow.pipelineagent.bitbucket.service.BitbucketAiClientService;
import org.rostilos.codecrow.pipelineagent.bitbucket.service.BitbucketReportingService;
import org.rostilos.codecrow.pipelineagent.generic.dto.request.ai.AiAnalysisRequest;
import org.rostilos.codecrow.pipelineagent.generic.exception.AnalysisLockedException;
import org.rostilos.codecrow.pipelineagent.generic.service.AnalysisLockService;
import org.rostilos.codecrow.pipelineagent.generic.service.PullRequestService;
import org.rostilos.codecrow.pipelineagent.generic.client.AiAnalysisClient;
import org.slf4j.Logger;
import org.slf4j.LoggerFactory;
import org.springframework.stereotype.Service;

import java.io.IOException;
import java.security.GeneralSecurityException;
import java.util.Map;
import java.util.Optional;

@Service
public class PullRequestAnalysisProcessor {
    private static final Logger log = LoggerFactory.getLogger(PullRequestAnalysisProcessor.class);


    private final CodeAnalysisService codeAnalysisService;
    private final BitbucketReportingService reportingService;
    private final PullRequestService pullRequestService;
    private final AiAnalysisClient aiAnalysisClient;
    private final BitbucketAiClientService bitbucketAiClientService;
    private final AnalysisLockService analysisLockService;

    public PullRequestAnalysisProcessor(
            PullRequestService pullRequestService,
            CodeAnalysisService codeAnalysisService,
            AiAnalysisClient aiAnalysisClient,
            BitbucketAiClientService bitbucketAiClientService,
            BitbucketReportingService reportingService,
            AnalysisLockService analysisLockService
    ) {
        this.codeAnalysisService = codeAnalysisService;
        this.reportingService = reportingService;
        this.pullRequestService = pullRequestService;
        this.aiAnalysisClient = aiAnalysisClient;
        this.bitbucketAiClientService = bitbucketAiClientService;
        this.analysisLockService = analysisLockService;
    }

    public Map<String, Object> process(
            PrProcessRequest request,
            BitbucketWebhookProcessor.EventConsumer consumer,
            Project project
    ) throws GeneralSecurityException {
        // Use the lock service's built-in retry mechanism
        // Convert EventConsumer to Consumer<Map<String, Object>>
        Optional<String> lockKey = analysisLockService.acquireLockWithWait(
                project,
                request.getSourceBranchName(),
                AnalysisLockType.PR_ANALYSIS,
                request.getCommitHash(),
                request.getPullRequestId(),
                consumer::accept
        );

        if (lockKey.isEmpty()) {
            String message = String.format(
                    "Failed to acquire lock after %d minutes for project=%s, PR=%d, branch=%s. Another analysis is still in progress.",
                    analysisLockService.getLockWaitTimeoutMinutes(),
                    project.getId(),
                    request.getPullRequestId(),
                    request.getSourceBranchName()
            );
            log.warn(message);
            throw new AnalysisLockedException(
                    AnalysisLockType.PR_ANALYSIS.name(),
                    request.getSourceBranchName(),
                    project.getId()
            );
        }

        try {
            PullRequest pullRequest = pullRequestService.createOrUpdatePullRequest(
                    request.getProjectId(),
                    request.getPullRequestId(),
                    request.getCommitHash(),
                    request.getSourceBranchName(),
                    request.getTargetBranchName(),
                    project
            );

            if(postAnalysisCacheIfExist(project, pullRequest, request.getCommitHash(), request.getPullRequestId())) {
                return Map.of("status", "cached", "cached", true);
            }

            Optional<CodeAnalysis> previousAnalysis = codeAnalysisService.getPreviousVersionCodeAnalysis(
                    project.getId(),
                    request.getPullRequestId()
            );

            AiAnalysisRequest aiRequest = bitbucketAiClientService.buildAiAnalysisRequest(project, request, previousAnalysis);

            Map<String, Object> aiResponse = aiAnalysisClient.performAnalysis(aiRequest, event -> {
                try {
                    log.debug("Received event from AI client: type={}", event.get("type"));
                    consumer.accept(event);
                    log.debug("Event forwarded to consumer successfully");
                } catch (Exception ex) {
                    log.error("Event consumer failed: {}", ex.getMessage(), ex);
                }
            });

            CodeAnalysis newAnalysis = codeAnalysisService.createAnalysisFromAiResponse(
                    project,
                    aiResponse,
                    request.getPullRequestId(),
                    request.getTargetBranchName(),
                    request.getSourceBranchName(),
                    request.getCommitHash()
            );

            try {
                reportingService.postAnalysisResults(
                        newAnalysis,
                        project,
                        request.getPullRequestId(),
                        pullRequest.getId()
                );
            } catch (IOException e) {
                log.error("Failed to post analysis results to Bitbucket: {}", e.getMessage(), e);
                consumer.accept(Map.of(
                        "type", "warning",
                        "message", "Analysis completed but failed to post results to Bitbucket: " + e.getMessage()
                ));
            }

            return aiResponse;
        } catch (IOException e) {
            log.error("IOException during PR analysis: {}", e.getMessage(), e);
            consumer.accept(Map.of(
                    "type", "error",
                    "message", "Analysis failed due to I/O error: " + e.getMessage()
            ));
            return Map.of("status", "error", "message", e.getMessage());
        } finally {
            analysisLockService.releaseLock(lockKey.get());
        }
    }

    protected boolean postAnalysisCacheIfExist(Project project, PullRequest pullRequest, String commitHash, Long prId) {
        Optional<CodeAnalysis> cachedAnalysis = codeAnalysisService.getCodeAnalysisCache(
                project.getId(),
                commitHash,
                prId
        );

        if (cachedAnalysis.isPresent()) {
            try {
                reportingService.postAnalysisResults(
                        cachedAnalysis.get(),
                        project,
                        prId,
                        pullRequest.getId()
                );
            } catch (IOException e) {
                log.error("Failed to post cached analysis results to Bitbucket: {}", e.getMessage(), e);
                // Don't fail the whole request just because posting failed
                // The analysis is already cached and can be retrieved
            }
            return true;
        }
        return false;
    }
}
