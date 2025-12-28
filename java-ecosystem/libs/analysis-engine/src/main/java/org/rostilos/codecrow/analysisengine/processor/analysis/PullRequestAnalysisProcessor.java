package org.rostilos.codecrow.analysisengine.processor.analysis;

import org.rostilos.codecrow.core.model.analysis.AnalysisLockType;
import org.rostilos.codecrow.core.model.codeanalysis.CodeAnalysis;
import org.rostilos.codecrow.core.model.project.Project;
import org.rostilos.codecrow.core.model.pullrequest.PullRequest;
import org.rostilos.codecrow.core.model.vcs.EVcsProvider;
import org.rostilos.codecrow.core.service.CodeAnalysisService;
import org.rostilos.codecrow.analysisengine.dto.request.processor.PrProcessRequest;
import org.rostilos.codecrow.analysisengine.dto.request.ai.AiAnalysisRequest;
import org.rostilos.codecrow.analysisengine.exception.AnalysisLockedException;
import org.rostilos.codecrow.analysisengine.service.AnalysisLockService;
import org.rostilos.codecrow.analysisengine.service.PullRequestService;
import org.rostilos.codecrow.analysisengine.service.vcs.VcsAiClientService;
import org.rostilos.codecrow.analysisengine.service.vcs.VcsReportingService;
import org.rostilos.codecrow.analysisengine.service.vcs.VcsServiceFactory;
import org.rostilos.codecrow.analysisengine.client.AiAnalysisClient;
import org.slf4j.Logger;
import org.slf4j.LoggerFactory;
import org.springframework.stereotype.Service;

import java.io.IOException;
import java.security.GeneralSecurityException;
import java.util.Map;
import java.util.Optional;

/**
 * Generic service that handles pull request analysis.
 * Uses VCS-specific services via VcsServiceFactory for provider-specific operations.
 */
@Service
public class PullRequestAnalysisProcessor {
    private static final Logger log = LoggerFactory.getLogger(PullRequestAnalysisProcessor.class);

    private final CodeAnalysisService codeAnalysisService;
    private final PullRequestService pullRequestService;
    private final AiAnalysisClient aiAnalysisClient;
    private final VcsServiceFactory vcsServiceFactory;
    private final AnalysisLockService analysisLockService;

    public PullRequestAnalysisProcessor(
            PullRequestService pullRequestService,
            CodeAnalysisService codeAnalysisService,
            AiAnalysisClient aiAnalysisClient,
            VcsServiceFactory vcsServiceFactory,
            AnalysisLockService analysisLockService
    ) {
        this.codeAnalysisService = codeAnalysisService;
        this.pullRequestService = pullRequestService;
        this.aiAnalysisClient = aiAnalysisClient;
        this.vcsServiceFactory = vcsServiceFactory;
        this.analysisLockService = analysisLockService;
    }

    public interface EventConsumer {
        void accept(Map<String, Object> event);
    }

    private EVcsProvider getVcsProvider(Project project) {
        if (project.getVcsBinding() != null && project.getVcsBinding().getVcsConnection() != null) {
            return project.getVcsBinding().getVcsConnection().getProviderType();
        }
        if (project.getVcsRepoBinding() != null && project.getVcsRepoBinding().getVcsConnection() != null) {
            return project.getVcsRepoBinding().getVcsConnection().getProviderType();
        }
        throw new IllegalStateException("No VCS connection configured for project: " + project.getId());
    }

    public Map<String, Object> process(
            PrProcessRequest request,
            EventConsumer consumer,
            Project project
    ) throws GeneralSecurityException {
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

            EVcsProvider provider = getVcsProvider(project);
            VcsReportingService reportingService = vcsServiceFactory.getReportingService(provider);

            if (postAnalysisCacheIfExist(project, pullRequest, request.getCommitHash(), request.getPullRequestId(), reportingService, request.getPlaceholderCommentId())) {
                return Map.of("status", "cached", "cached", true);
            }

            Optional<CodeAnalysis> previousAnalysis = codeAnalysisService.getPreviousVersionCodeAnalysis(
                    project.getId(),
                    request.getPullRequestId()
            );

            VcsAiClientService aiClientService = vcsServiceFactory.getAiClientService(provider);
            AiAnalysisRequest aiRequest = aiClientService.buildAiAnalysisRequest(project, request, previousAnalysis);

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
                        pullRequest.getId(),
                        request.getPlaceholderCommentId()
                );
            } catch (IOException e) {
                log.error("Failed to post analysis results to VCS: {}", e.getMessage(), e);
                consumer.accept(Map.of(
                        "type", "warning",
                        "message", "Analysis completed but failed to post results to VCS: " + e.getMessage()
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

    protected boolean postAnalysisCacheIfExist(
            Project project, 
            PullRequest pullRequest, 
            String commitHash, 
            Long prId,
            VcsReportingService reportingService,
            String placeholderCommentId
    ) {
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
                        pullRequest.getId(),
                        placeholderCommentId
                );
            } catch (IOException e) {
                log.error("Failed to post cached analysis results to VCS: {}", e.getMessage(), e);
            }
            return true;
        }
        return false;
    }
}
