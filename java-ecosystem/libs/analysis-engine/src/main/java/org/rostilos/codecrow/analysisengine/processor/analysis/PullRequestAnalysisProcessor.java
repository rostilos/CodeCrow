package org.rostilos.codecrow.analysisengine.processor.analysis;

import org.rostilos.codecrow.analysisengine.util.ProjectVcsInfoRetriever;
import org.rostilos.codecrow.core.model.analysis.AnalysisLockType;
import org.rostilos.codecrow.core.model.codeanalysis.CodeAnalysis;
import org.rostilos.codecrow.core.model.codeanalysis.CodeAnalysisIssue;
import org.rostilos.codecrow.core.model.project.Project;
import org.rostilos.codecrow.core.model.pullrequest.PullRequest;
import org.rostilos.codecrow.core.model.vcs.EVcsProvider;
import org.rostilos.codecrow.core.model.vcs.VcsRepoInfo;
import org.rostilos.codecrow.core.service.CodeAnalysisService;
import org.rostilos.codecrow.filecontent.service.FileSnapshotService;
import org.rostilos.codecrow.analysisengine.service.pr.PrIssueTrackingService;
import org.rostilos.codecrow.analysisengine.service.AstScopeEnricher;
import org.rostilos.codecrow.analysisengine.dto.request.processor.PrProcessRequest;
import org.rostilos.codecrow.analysisengine.dto.request.ai.AiAnalysisRequest;
import org.rostilos.codecrow.analysisengine.dto.request.ai.AiAnalysisRequestImpl;
import org.rostilos.codecrow.analysisengine.dto.request.ai.enrichment.FileContentDto;
import org.rostilos.codecrow.analysisengine.dto.request.ai.enrichment.PrEnrichmentDataDto;
import org.rostilos.codecrow.analysisengine.exception.AnalysisLockedException;
import org.rostilos.codecrow.analysisengine.service.AnalysisLockService;
import org.rostilos.codecrow.analysisengine.service.PullRequestService;
import org.rostilos.codecrow.analysisapi.rag.RagOperationsService;
import org.rostilos.codecrow.commitgraph.service.AnalyzedCommitService;
import org.rostilos.codecrow.analysisengine.service.vcs.VcsAiClientService;
import org.rostilos.codecrow.analysisengine.service.vcs.VcsReportingService;
import org.rostilos.codecrow.analysisengine.service.vcs.VcsServiceFactory;
import org.rostilos.codecrow.analysisengine.aiclient.AiAnalysisClient;
import org.rostilos.codecrow.analysisengine.policy.ExecutionLifecycle;
import org.rostilos.codecrow.analysisengine.policy.ExecutionPolicyRuntime;
import org.rostilos.codecrow.analysisengine.policy.FrozenExecutionPlan;
import org.rostilos.codecrow.analysisengine.policy.PublicationKey;
import org.rostilos.codecrow.analysisengine.policy.PublicationReservation;
import org.rostilos.codecrow.analysisengine.policy.StableRolloutKey;
import org.rostilos.codecrow.analysisengine.telemetry.PipelineTelemetryFinalizer;
import org.rostilos.codecrow.analysisengine.telemetry.PipelineTelemetryFinalizer.StageObservation;
import org.rostilos.codecrow.events.analysis.AnalysisStartedEvent;
import org.rostilos.codecrow.events.analysis.AnalysisCompletedEvent;
import org.slf4j.Logger;
import org.slf4j.LoggerFactory;
import org.springframework.beans.factory.annotation.Autowired;
import org.springframework.context.ApplicationEventPublisher;
import org.springframework.stereotype.Service;

import java.io.IOException;
import java.security.GeneralSecurityException;
import java.time.Duration;
import java.time.Instant;
import java.util.HashMap;
import java.util.LinkedHashMap;
import java.util.List;
import java.util.Map;
import java.util.Optional;
import java.util.Collections;
import java.util.function.Consumer;
import java.util.regex.Pattern;
import java.util.stream.Collectors;

import org.rostilos.codecrow.analysisengine.util.DiffFingerprintUtil;
import org.rostilos.codecrow.vcsclient.VcsClient;
import org.rostilos.codecrow.vcsclient.VcsClientProvider;

/**
 * Generic service that handles pull request analysis.
 * Uses VCS-specific services via VcsServiceFactory for provider-specific
 * operations.
 */
@Service
public class PullRequestAnalysisProcessor {
    private static final Logger log = LoggerFactory.getLogger(PullRequestAnalysisProcessor.class);
    private static final Pattern EXACT_INDEX_VERSION = Pattern.compile(
            "(?:rag-disabled|rag-commit-[0-9a-f]{40,64})");

    private final CodeAnalysisService codeAnalysisService;
    private final PullRequestService pullRequestService;
    private final AiAnalysisClient aiAnalysisClient;
    private final VcsServiceFactory vcsServiceFactory;
    private final AnalysisLockService analysisLockService;
    private final RagOperationsService ragOperationsService;
    private final ApplicationEventPublisher eventPublisher;
    private final AnalyzedCommitService analyzedCommitService;
    private final VcsClientProvider vcsClientProvider;
    private final FileSnapshotService fileSnapshotService;
    private final PrIssueTrackingService prIssueTrackingService;
    private final AstScopeEnricher astScopeEnricher;
    private final ExecutionPolicyRuntime executionPolicyRuntime;

    public PullRequestAnalysisProcessor(
            PullRequestService pullRequestService,
            CodeAnalysisService codeAnalysisService,
            AiAnalysisClient aiAnalysisClient,
            VcsServiceFactory vcsServiceFactory,
            AnalysisLockService analysisLockService,
            AnalyzedCommitService analyzedCommitService,
            VcsClientProvider vcsClientProvider,
            FileSnapshotService fileSnapshotService,
            PrIssueTrackingService prIssueTrackingService,
            AstScopeEnricher astScopeEnricher,
            @Autowired(required = false) RagOperationsService ragOperationsService,
            @Autowired(required = false) ApplicationEventPublisher eventPublisher
    ) {
        this(
                pullRequestService,
                codeAnalysisService,
                aiAnalysisClient,
                vcsServiceFactory,
                analysisLockService,
                analyzedCommitService,
                vcsClientProvider,
                fileSnapshotService,
                prIssueTrackingService,
                astScopeEnricher,
                ragOperationsService,
                eventPublisher,
                null);
    }

    @Autowired
    public PullRequestAnalysisProcessor(
            PullRequestService pullRequestService,
            CodeAnalysisService codeAnalysisService,
            AiAnalysisClient aiAnalysisClient,
            VcsServiceFactory vcsServiceFactory,
            AnalysisLockService analysisLockService,
            AnalyzedCommitService analyzedCommitService,
            VcsClientProvider vcsClientProvider,
            FileSnapshotService fileSnapshotService,
            PrIssueTrackingService prIssueTrackingService,
            AstScopeEnricher astScopeEnricher,
            @Autowired(required = false) RagOperationsService ragOperationsService,
            @Autowired(required = false) ApplicationEventPublisher eventPublisher,
            @Autowired(required = false) ExecutionPolicyRuntime executionPolicyRuntime
    ) {
        this.codeAnalysisService = codeAnalysisService;
        this.pullRequestService = pullRequestService;
        this.aiAnalysisClient = aiAnalysisClient;
        this.vcsServiceFactory = vcsServiceFactory;
        this.analysisLockService = analysisLockService;
        this.ragOperationsService = ragOperationsService;
        this.eventPublisher = eventPublisher;
        this.analyzedCommitService = analyzedCommitService;
        this.vcsClientProvider = vcsClientProvider;
        this.fileSnapshotService = fileSnapshotService;
        this.prIssueTrackingService = prIssueTrackingService;
        this.astScopeEnricher = astScopeEnricher;
        this.executionPolicyRuntime = executionPolicyRuntime;
    }

    public interface EventConsumer {
        void accept(Map<String, Object> event);
    }

    public Map<String, Object> process(
            PrProcessRequest request,
            EventConsumer consumer,
            Project project
    ) throws GeneralSecurityException {
        Instant startTime = Instant.now();
        String correlationId = java.util.UUID.randomUUID().toString();
        FrozenExecutionPlan policyPlan = freezePolicyPlan(project, request);
        ExecutionLifecycle policyLifecycle = policyPlan == null
                ? null
                : new ExecutionLifecycle(policyPlan.primary());
        emitPolicySelection(consumer, policyPlan);

        // Publish analysis started event
        publishAnalysisStartedEvent(project, request, correlationId);

        // Check if a lock was already acquired by the caller (e.g., webhook handler)
        // to prevent double-locking which causes unnecessary 2-minute waits
        String lockKey;
        boolean isPreAcquired = false;
        if (request.getPreAcquiredLockKey() != null && !request.getPreAcquiredLockKey().isBlank()) {
            lockKey = request.getPreAcquiredLockKey();
            isPreAcquired = true;
            log.info("Using pre-acquired lock: {} for project={}, PR={}", lockKey, project.getId(),
                    request.getPullRequestId());
        } else {
            Optional<String> acquiredLock = analysisLockService.acquireLockWithWait(
                    project,
                    request.getSourceBranchName(),
                    AnalysisLockType.PR_ANALYSIS,
                    request.getCommitHash(),
                    request.getPullRequestId(),
                    consumer::accept);

            if (acquiredLock.isEmpty()) {
                String message = String.format(
                        "Failed to acquire lock after %d minutes for project=%s, PR=%d, branch=%s. Another analysis is still in progress.",
                        analysisLockService.getLockWaitTimeoutMinutes(),
                        project.getId(),
                        request.getPullRequestId(),
                        request.getSourceBranchName());
                log.warn(message);

                // Publish failed event due to lock timeout
                publishAnalysisCompletedEvent(project, request, correlationId, startTime,
                        AnalysisCompletedEvent.CompletionStatus.FAILED, 0, 0, "Lock acquisition timeout");
                failPolicyLifecycle(policyLifecycle);

                throw new AnalysisLockedException(
                        AnalysisLockType.PR_ANALYSIS.name(),
                        request.getSourceBranchName(),
                        project.getId());
            }
            lockKey = acquiredLock.get();
        }

        try {
            if (policyLifecycle != null) {
                policyLifecycle.start();
            }
            if (cancelRequested(policyLifecycle)) {
                return cancelledResult(project, request, correlationId, startTime, consumer);
            }
            EVcsProvider provider = ProjectVcsInfoRetriever.getVcsProvider(project);
            VcsReportingService reportingService = vcsServiceFactory.getReportingService(provider);
            PullRequest pullRequest = pullRequestService.createOrUpdatePullRequest(
                    request.getProjectId(),
                    request.getPullRequestId(),
                    request.getCommitHash(),
                    request.getSourceBranchName(),
                    request.getTargetBranchName(),
                    project);

            CacheHitType cacheHit = postAnalysisCacheIfExist(project, pullRequest, request.getCommitHash(), request.getPullRequestId(),
                    reportingService, request.getPlaceholderCommentId(), request.getTargetBranchName(),
                    request.getSourceBranchName(), consumer, policyPlan);
            if (cacheHit != CacheHitType.NONE) {
                emitStageTelemetry(consumer, "acquisition", "java_analysis_cache", "skipped",
                        startTime, 0, "analysis_cache_hit");
                emitStageTelemetry(consumer, "retrieval", "java_analysis_cache", "skipped",
                        startTime, 0, "analysis_cache_hit");
                emitStageTelemetry(consumer, "persistence", "java_analysis_cache", "skipped",
                        startTime, 0, "analysis_cache_hit");
                publishAnalysisCompletedEvent(project, request, correlationId, startTime,
                        AnalysisCompletedEvent.CompletionStatus.SUCCESS, 0, 0, null);
                completePolicyLifecycle(policyLifecycle);
                String cacheStatus = cacheHit == CacheHitType.COMMIT_HASH ? "cached_by_commit" : "cached";
                return Map.of("status", cacheStatus, "cached", true);
            }

            // Get all previous analyses for this PR to provide full issue history to AI
            List<CodeAnalysis> allPrAnalyses = codeAnalysisService.getAllPrAnalyses(
                    project.getId(),
                    request.getPullRequestId());

            // Get the most recent analysis for incremental diff calculation
            Optional<CodeAnalysis> previousAnalysis = allPrAnalyses.isEmpty()
                    ? Optional.empty()
                    : Optional.of(allPrAnalyses.get(0));

            // Ensure branch index exists for TARGET branch (e.g., "1.2.1-rc")
            // This is where the PR will merge TO - we want RAG context from this branch
            Instant retrievalStartedAt = Instant.now();
            String retrievalReason = ensureRagIndexForTargetBranch(
                    project, request.getTargetBranchName(), consumer);
            String retrievalOutcome;
            if (retrievalReason == null) {
                retrievalOutcome = "complete";
            } else if ("rag_index_refresh_failed".equals(retrievalReason)) {
                retrievalOutcome = "failed";
            } else {
                retrievalOutcome = "skipped";
            }
            emitStageTelemetry(
                    consumer,
                    "retrieval",
                    "java_rag_index",
                    retrievalOutcome,
                    retrievalStartedAt,
                    0,
                    retrievalReason);
            String indexVersion = resolveIndexVersion(
                    project, request.getTargetBranchName());
            StageObservation retrievalTerminalStage = terminalStage(
                    "retrieval",
                    "java_rag_index",
                    retrievalOutcome,
                    retrievalStartedAt,
                    0,
                    retrievalReason);

            VcsAiClientService aiClientService = vcsServiceFactory.getAiClientService(provider);
            Instant acquisitionStartedAt = Instant.now();
            List<AiAnalysisRequest> aiRequests;
            try {
                aiRequests = aiClientService.buildAiAnalysisRequests(
                        project, request, previousAnalysis, allPrAnalyses);
            } catch (GeneralSecurityException | RuntimeException error) {
                emitStageTelemetry(
                        consumer,
                        "acquisition",
                        "java_vcs_diff",
                        "failed",
                        acquisitionStartedAt,
                        0,
                        "diff_acquisition_failed");
                throw error;
            }

            if (aiRequests == null || aiRequests.isEmpty()) {
                String message = "No changed files match the project analysis scope";
                emitStageTelemetry(
                        consumer,
                        "acquisition",
                        "java_vcs_diff",
                        "skipped",
                        acquisitionStartedAt,
                        0,
                        "no_analyzable_changes");
                log.info("Skipping PR analysis for project={}, PR={}: {}",
                        project.getId(), request.getPullRequestId(), message);
                consumer.accept(Map.of("type", "info", "message", message));
                publishAnalysisCompletedEvent(project, request, correlationId, startTime,
                        AnalysisCompletedEvent.CompletionStatus.SUCCESS, 0, 0, null);
                completePolicyLifecycle(policyLifecycle);
                return Map.of("status", "ignored", "message", message);
            }

            AiAnalysisRequest aiRequest = aiRequests.get(0);
            List<String> changedFiles = aiRequest.getChangedFiles() == null
                    ? List.of()
                    : aiRequest.getChangedFiles();
            emitStageTelemetry(
                    consumer,
                    "acquisition",
                    "java_vcs_diff",
                    "complete",
                    acquisitionStartedAt,
                    changedFiles.size(),
                    null);
            StageObservation acquisitionTerminalStage = terminalStage(
                    "acquisition",
                    "java_vcs_diff",
                    "complete",
                    acquisitionStartedAt,
                    changedFiles.size(),
                    null);
            String diffFingerprint = DiffFingerprintUtil.compute(aiRequest.getRawDiff());

            if (postDiffFingerprintCacheIfExist(
                    request, diffFingerprint, project, pullRequest, aiRequest, reportingService, consumer,
                    policyPlan)) {
                publishAnalysisCompletedEvent(project, request, correlationId, startTime,
                        AnalysisCompletedEvent.CompletionStatus.SUCCESS, 0, 0, null);
                completePolicyLifecycle(policyLifecycle);
                return Map.of("status", "cached_by_fingerprint", "cached", true);
            }

            if (cancelRequested(policyLifecycle)) {
                return cancelledResult(project, request, correlationId, startTime, consumer);
            }
            Consumer<Map<String, Object>> aiEventConsumer = event -> {
                try {
                    log.debug("Received event from AI client: type={}", event.get("type"));
                    consumer.accept(event);
                    log.debug("Event forwarded to consumer successfully");
                } catch (Exception ex) {
                    log.error("Event consumer failed: {}", ex.getMessage(), ex);
                }
            };
            Map<String, Object> aiResponse = performAiAnalysis(
                    aiRequest, aiEventConsumer, policyPlan, indexVersion);

            if (cancelRequested(policyLifecycle)) {
                Map<String, Object> cancelled = cancelledResult(
                        project, request, correlationId, startTime, consumer);
                Map<String, Object> finalized = finalizePipelineTelemetry(
                        aiResponse,
                        consumer,
                        startTime,
                        List.of(
                                acquisitionTerminalStage,
                                retrievalTerminalStage,
                                terminalStage(
                                        "persistence",
                                        "java_analysis_store",
                                        "skipped",
                                        Instant.now(),
                                        0,
                                        "kill_switch"),
                                terminalStage(
                                        "delivery",
                                        "java_vcs_reporting",
                                        "skipped",
                                        Instant.now(),
                                        0,
                                        "kill_switch")));
                return attachFinalizedTelemetry(cancelled, finalized);
            }

            // === Extract file contents from enrichment data for line hash computation ===
            Map<String, String> fileContents = new java.util.HashMap<>(extractFileContents(aiRequest));
            java.util.Set<String> allChangedFiles = new java.util.HashSet<>(changedFiles);

            // === VCS fallback: when enrichment data is empty (disabled, failed, or
            // provider-specific),
            // fetch file contents directly from VCS to ensure source viewer always has data
            // ===
            if (fileContents.isEmpty()) {
                log.info(
                        "Enrichment file contents empty — falling back to direct VCS file fetch for PR {} (project={})",
                        request.getPullRequestId(), project.getId());
                fileContents = fetchFileContentsFromVcs(project, new java.util.ArrayList<>(allChangedFiles),
                        request.getCommitHash());
            }

            Instant persistenceStartedAt = Instant.now();
            CodeAnalysis newAnalysis;
            try {
                newAnalysis = codeAnalysisService.createAnalysisFromAiResponse(
                        project,
                        aiResponse,
                        request.getPullRequestId(),
                        request.getTargetBranchName(),
                        request.getSourceBranchName(),
                        request.getCommitHash(),
                        request.getPrAuthorId(),
                        request.getPrAuthorUsername(),
                        diffFingerprint,
                        fileContents,
                        taskContextValue(aiRequest, "task_key", "taskKey", "key"),
                        taskContextValue(aiRequest, "task_summary", "taskSummary", "summary"));
            } catch (RuntimeException error) {
                emitStageTelemetry(
                        consumer,
                        "persistence",
                        "java_analysis_store",
                        "failed",
                        persistenceStartedAt,
                        0,
                        "analysis_persistence_failed");
                finalizePipelineTelemetry(
                        aiResponse,
                        consumer,
                        startTime,
                        List.of(
                                acquisitionTerminalStage,
                                retrievalTerminalStage,
                                terminalStage(
                                        "persistence",
                                        "java_analysis_store",
                                        "failed",
                                        persistenceStartedAt,
                                        0,
                                        "analysis_persistence_failed"),
                                terminalStage(
                                        "delivery",
                                        "java_vcs_reporting",
                                        "skipped",
                                        Instant.now(),
                                        0,
                                        "upstream_failed")));
                throw error;
            }

            int issuesFound = newAnalysis.getIssues() != null ? newAnalysis.getIssues().size() : 0;
            emitStageTelemetry(
                    consumer,
                    "persistence",
                    "java_analysis_store",
                    "complete",
                    persistenceStartedAt,
                    issuesFound,
                    null);
            StageObservation persistenceTerminalStage = terminalStage(
                    "persistence",
                    "java_analysis_store",
                    "complete",
                    persistenceStartedAt,
                    issuesFound,
                    null);

            // === AST scope enrichment: resolve scope boundaries for each issue ===
            try {
                if (newAnalysis.getIssues() != null && !newAnalysis.getIssues().isEmpty()) {
                    astScopeEnricher.enrichWithAstScopes(newAnalysis.getIssues(), fileContents);
                }
            } catch (Exception astEx) {
                log.warn("AST scope enrichment failed (non-critical): {}", astEx.getMessage());
            }

            // === Persist file snapshots at PR level for the source code viewer ===
            // Accumulates across iterations: 2nd run adds new files, keeps old ones.
            try {
                fileSnapshotService.persistSnapshotsForPr(pullRequest, newAnalysis, fileContents,
                        request.getCommitHash());
            } catch (Exception snapEx) {
                log.warn("Failed to persist file snapshots (non-critical): {}", snapEx.getMessage());
            }

            // === Deterministic PR issue tracking against previous iteration ===
            try {
                if (previousAnalysis.isPresent()) {
                    Map<String, String> prevFileContents = fileSnapshotService.getFileContentsMap(
                            previousAnalysis.get().getId());
                    prIssueTrackingService.trackPrIteration(
                            newAnalysis, previousAnalysis.get(), fileContents, prevFileContents);
                }
            } catch (Exception trackEx) {
                log.warn("PR issue tracking failed (non-critical): {}", trackEx.getMessage());
            }

            if (cancelRequested(policyLifecycle)) {
                Map<String, Object> cancelled = cancelledResult(
                        project, request, correlationId, startTime, consumer);
                Map<String, Object> finalized = finalizePipelineTelemetry(
                        aiResponse,
                        consumer,
                        startTime,
                        List.of(
                                acquisitionTerminalStage,
                                retrievalTerminalStage,
                                persistenceTerminalStage,
                                terminalStage(
                                        "delivery",
                                        "java_vcs_reporting",
                                        "skipped",
                                        Instant.now(),
                                        issuesFound,
                                        "kill_switch")));
                return attachFinalizedTelemetry(cancelled, finalized);
            }

            Instant deliveryStartedAt = Instant.now();
            StageObservation deliveryTerminalStage;
            try {
                boolean published = publishAnalysisResults(
                        policyPlan,
                        consumer,
                        deliveryStartedAt,
                        issuesFound,
                        reportingService,
                        newAnalysis,
                        project,
                        request.getPullRequestId(),
                        pullRequest.getId(),
                        request.getPlaceholderCommentId(),
                        request.getCommitHash());
                deliveryTerminalStage = terminalStage(
                        "delivery",
                        "java_vcs_reporting",
                        published ? "complete" : "skipped",
                        deliveryStartedAt,
                        issuesFound,
                        published ? null : "publication_fence_denied");
            } catch (IOException e) {
                emitStageTelemetry(
                        consumer,
                        "delivery",
                        "java_vcs_reporting",
                        "failed",
                        deliveryStartedAt,
                        issuesFound,
                        "vcs_delivery_failed");
                deliveryTerminalStage = terminalStage(
                        "delivery",
                        "java_vcs_reporting",
                        "failed",
                        deliveryStartedAt,
                        issuesFound,
                        "vcs_delivery_failed");
                log.error("Failed to post analysis results to VCS: {}", e.getMessage(), e);
                try {
                    consumer.accept(Map.of(
                            "type", "warning",
                            "message", "Analysis completed but failed to post results to VCS: " + e.getMessage()));
                } catch (RuntimeException eventError) {
                    log.warn("VCS delivery warning emission failed: {}",
                            eventError.getClass().getSimpleName());
                }
            }

            // === DAG: Mark PR commits as ANALYZED ===
            markPrCommitsAnalyzed(project, request.getSourceBranchName(), request.getCommitHash(), newAnalysis);

            // Publish successful completion event
            publishAnalysisCompletedEvent(project, request, correlationId, startTime,
                    AnalysisCompletedEvent.CompletionStatus.SUCCESS, issuesFound,
                    allChangedFiles.size(), null);
            completePolicyLifecycle(policyLifecycle);

            return finalizePipelineTelemetry(
                    aiResponse,
                    consumer,
                    startTime,
                    List.of(
                            acquisitionTerminalStage,
                            retrievalTerminalStage,
                            persistenceTerminalStage,
                            deliveryTerminalStage));
        } catch (IOException e) {
            failPolicyLifecycle(policyLifecycle);
            log.error("IOException during PR analysis: {}", e.getMessage(), e);
            consumer.accept(Map.of(
                    "type", "error",
                    "message", "Analysis failed due to I/O error: " + e.getMessage()));

            // Publish failed event
            publishAnalysisCompletedEvent(project, request, correlationId, startTime,
                    AnalysisCompletedEvent.CompletionStatus.FAILED, 0, 0, e.getMessage());

            return Map.of("status", "error", "message", e.getMessage());
        } catch (GeneralSecurityException | RuntimeException error) {
            failPolicyLifecycle(policyLifecycle);
            throw error;
        } finally {
            if (!isPreAcquired) {
                analysisLockService.releaseLock(lockKey);
            }
        }
    }

    private FrozenExecutionPlan freezePolicyPlan(Project project, PrProcessRequest request) {
        if (executionPolicyRuntime == null) {
            return null;
        }
        Long projectId = project.getId();
        if (projectId == null || projectId <= 0 || request.getPullRequestId() == null) {
            throw new IllegalArgumentException("policy selection requires persisted project and pull request IDs");
        }
        if (project.getWorkspace() == null || project.getWorkspace().getId() == null
                || project.getWorkspace().getId() <= 0) {
            throw new IllegalArgumentException("policy selection requires a persisted workspace ID");
        }
        String identityInput = projectId + "\n"
                + request.getPullRequestId() + "\n"
                + request.getCommitHash() + "\n"
                + String.valueOf(request.getAnalysisType());
        String executionId = "pr:" + sha256(identityInput);
        return executionPolicyRuntime.freeze(
                executionId,
                StableRolloutKey.forProject(project.getWorkspace().getId(), projectId));
    }

    private Map<String, Object> performAiAnalysis(
            AiAnalysisRequest aiRequest,
            Consumer<Map<String, Object>> aiEventConsumer,
            FrozenExecutionPlan policyPlan,
            String indexVersion) throws IOException, GeneralSecurityException {
        boolean exactIndex = indexVersion != null
                && EXACT_INDEX_VERSION.matcher(indexVersion).matches();
        if (exactIndex) {
            return aiAnalysisClient.performAnalysis(
                    aiRequest,
                    aiEventConsumer,
                    policyPlan == null ? null : policyPlan.primary(),
                    indexVersion);
        }
        return policyPlan == null
                ? aiAnalysisClient.performAnalysis(aiRequest, aiEventConsumer)
                : aiAnalysisClient.performAnalysis(aiRequest, aiEventConsumer, policyPlan.primary());
    }

    private String sha256(String value) {
        return org.rostilos.codecrow.analysisengine.policy.PolicyHashing.sha256(value);
    }

    private boolean cancelRequested(ExecutionLifecycle lifecycle) {
        if (lifecycle == null || executionPolicyRuntime == null) {
            return false;
        }
        if (lifecycle.reconcileKillSwitch(executionPolicyRuntime.currentConfig())) {
            lifecycle.markCancelled();
            return true;
        }
        return lifecycle.state()
                == org.rostilos.codecrow.analysisengine.policy.ExecutionLifecycleState.CANCELLED;
    }

    private Map<String, Object> cancelledResult(
            Project project,
            PrProcessRequest request,
            String correlationId,
            Instant startTime,
            EventConsumer consumer) {
        emitStageTelemetry(
                consumer,
                "policy",
                "java_execution_policy",
                "cancelled",
                startTime,
                0,
                "kill_switch");
        publishAnalysisCompletedEvent(
                project,
                request,
                correlationId,
                startTime,
                AnalysisCompletedEvent.CompletionStatus.CANCELLED,
                0,
                0,
                "Policy kill switch");
        return Map.of("status", "cancelled", "reason", "policy_kill_switch");
    }

    private void completePolicyLifecycle(ExecutionLifecycle lifecycle) {
        if (lifecycle != null) {
            lifecycle.complete();
        }
    }

    private void failPolicyLifecycle(ExecutionLifecycle lifecycle) {
        if (lifecycle != null) {
            lifecycle.fail();
        }
    }

    private void emitPolicySelection(EventConsumer consumer, FrozenExecutionPlan plan) {
        if (plan == null) {
            return;
        }
        try {
            consumer.accept(Map.of(
                    "type", "policy_selection",
                    "schemaVersion", 1,
                    "policyVersion", plan.primary().policyVersion(),
                    "mode", plan.primary().mode().name().toLowerCase(java.util.Locale.ROOT),
                    "reason", plan.primary().selectionReason().name().toLowerCase(java.util.Locale.ROOT),
                    "configRevision", plan.configRevision(),
                    "shadowPlanned", plan.shadow() != null));
        } catch (RuntimeException error) {
            log.warn("Policy selection event emission failed: {}", error.getClass().getSimpleName());
        }
    }

    private String taskContextValue(AiAnalysisRequest aiRequest, String... keys) {
        Map<String, String> taskContext = aiRequest.getTaskContext();
        if (taskContext == null || taskContext.isEmpty()) {
            return null;
        }
        for (String key : keys) {
            String value = taskContext.get(key);
            if (value != null && !value.isBlank()) {
                return value.trim();
            }
        }
        return null;
    }

    /**
     * Extract file contents from the AI analysis request's enrichment data.
     * Returns a map of filePath → raw file content suitable for line hash
     * computation.
     */
    private Map<String, String> extractFileContents(AiAnalysisRequest aiRequest) {
        if (!(aiRequest instanceof AiAnalysisRequestImpl impl)) {
            return Collections.emptyMap();
        }
        PrEnrichmentDataDto enrichment = impl.getEnrichmentData();
        if (enrichment == null || enrichment.fileContents() == null) {
            return Collections.emptyMap();
        }
        Map<String, String> result = enrichment.fileContents().stream()
                .filter(f -> !f.skipped() && f.content() != null)
                .collect(Collectors.toMap(
                        FileContentDto::path,
                        FileContentDto::content,
                        (a, b) -> a // in case of duplicates, keep first
                ));
        log.debug("Extracted {} file contents from enrichment data for line hash computation", result.size());
        return result;
    }

    /**
     * Fetch file contents directly from VCS when enrichment data is empty.
     * This is the fallback path that ensures file snapshots are always available
     * for the source code viewer, regardless of enrichment status.
     *
     * @param project      the project with VCS connection info
     * @param changedFiles list of file paths to fetch
     * @param commitHash   the commit to fetch files from
     * @return map of filePath → raw content (empty map on failure)
     */
    private Map<String, String> fetchFileContentsFromVcs(Project project, List<String> changedFiles,
                                                         String commitHash) {
        if (changedFiles == null || changedFiles.isEmpty()) {
            return Collections.emptyMap();
        }
        try {
            VcsRepoInfo repoInfo = project.getEffectiveVcsRepoInfo();
            if (repoInfo == null || repoInfo.getVcsConnection() == null) {
                log.warn("No VCS repo info available — cannot fetch file contents for source viewer");
                return Collections.emptyMap();
            }
            VcsClient vcsClient = vcsClientProvider.getClient(repoInfo.getVcsConnection());
            Map<String, String> contents = vcsClient.getFileContents(
                    repoInfo.getRepoWorkspace(),
                    repoInfo.getRepoSlug(),
                    changedFiles,
                    commitHash,
                    100_000 // 100 KB max per file, consistent with enrichment service
            );
            log.info("VCS fallback: fetched {}/{} file contents for source viewer (commit={})",
                    contents.size(), changedFiles.size(),
                    commitHash != null ? commitHash.substring(0, Math.min(7, commitHash.length())) : "null");
            return contents;
        } catch (Exception e) {
            log.warn("VCS fallback file fetch failed (non-critical): {}", e.getMessage());
            return Collections.emptyMap();
        }
    }

    /**
     * Ensure PR-level file snapshots exist after a cache-hit clone.
     * <p>
     * Strategy:
     * <ol>
     * <li>Copy PR-level snapshots from the source analysis's original PR (fast, no
     * VCS calls)</li>
     * <li>If source PR has no snapshots, fetch from VCS using the provided
     * changed-file list
     * or, as a last resort, file paths extracted from the cloned analysis's
     * issues</li>
     * </ol>
     *
     * @param pullRequest    the current PR to persist snapshots for
     * @param cloned         the cloned analysis
     * @param sourceAnalysis the original (cache-hit) analysis
     * @param project        the project
     * @param commitHash     the commit hash for VCS fallback
     * @param changedFiles   explicit changed-file list (may be null for commit-hash
     *                       cache)
     */
    private void persistPrSnapshotsForCacheHit(PullRequest pullRequest, CodeAnalysis cloned,
                                               CodeAnalysis sourceAnalysis, Project project,
                                               String commitHash, List<String> changedFiles) {
        try {
            // Strategy 1: Copy PR-level snapshots from the source analysis's original PR
            if (sourceAnalysis.getPrNumber() != null) {
                Optional<PullRequest> sourcePr = pullRequestService.findPullRequest(
                        project.getId(), sourceAnalysis.getPrNumber());
                if (sourcePr.isPresent()) {
                    Map<String, String> sourceContents = fileSnapshotService.getFileContentsMapForPr(
                            sourcePr.get().getId());
                    if (!sourceContents.isEmpty()) {
                        fileSnapshotService.persistSnapshotsForPr(pullRequest, cloned, sourceContents, commitHash);
                        log.info("Copied {} PR snapshots from source PR {} to PR {} (cache hit)",
                                sourceContents.size(), sourceAnalysis.getPrNumber(), pullRequest.getPrNumber());
                        return;
                    }
                }
            }

            // Strategy 2: Fetch from VCS using explicit file list or issue file paths
            List<String> filePaths = changedFiles;
            if (filePaths == null || filePaths.isEmpty()) {
                filePaths = cloned.getIssues().stream()
                        .map(CodeAnalysisIssue::getFilePath)
                        .filter(fp -> fp != null && !fp.isBlank())
                        .distinct()
                        .collect(Collectors.toList());
            }
            if (!filePaths.isEmpty()) {
                Map<String, String> fileContents = fetchFileContentsFromVcs(project, filePaths, commitHash);
                if (!fileContents.isEmpty()) {
                    fileSnapshotService.persistSnapshotsForPr(pullRequest, cloned, fileContents, commitHash);
                }
            }
        } catch (Exception e) {
            log.warn("Failed to persist PR snapshots for cache hit (non-critical): {}", e.getMessage());
        }
    }

    protected boolean postDiffFingerprintCacheIfExist(
            PrProcessRequest request,
            String diffFingerprint,
            Project project,
            PullRequest pullRequest,
            AiAnalysisRequest aiRequest,
            VcsReportingService reportingService
    ) {
        return postDiffFingerprintCacheIfExist(
                request,
                diffFingerprint,
                project,
                pullRequest,
                aiRequest,
                reportingService,
                event -> { },
                null);
    }

    protected boolean postDiffFingerprintCacheIfExist(
            PrProcessRequest request,
            String diffFingerprint,
            Project project,
            PullRequest pullRequest,
            AiAnalysisRequest aiRequest,
            VcsReportingService reportingService,
            EventConsumer consumer

    ) {
        return postDiffFingerprintCacheIfExist(
                request,
                diffFingerprint,
                project,
                pullRequest,
                aiRequest,
                reportingService,
                consumer,
                null);
    }

    private boolean postDiffFingerprintCacheIfExist(
            PrProcessRequest request,
            String diffFingerprint,
            Project project,
            PullRequest pullRequest,
            AiAnalysisRequest aiRequest,
            VcsReportingService reportingService,
            EventConsumer consumer,
            FrozenExecutionPlan policyPlan
    ) {
        // Get analysis cache by diff fingerprint (any PR ID) - less ideal than commit hash but still a win
        if(diffFingerprint == null) {
            return false;
        }
        Optional<CodeAnalysis> fingerprintHit = codeAnalysisService.getAnalysisByDiffFingerprint(
                project.getId(), diffFingerprint);
        if(!fingerprintHit.isPresent()) {
            return false;
        }
        log.info(
                "Diff fingerprint cache hit for project={}, fingerprint={} (source PR={}). Cloning for PR={}.",
                project.getId(), diffFingerprint.substring(0, 8) + "...",
                fingerprintHit.get().getPrNumber(), request.getPullRequestId());
        Instant persistenceStartedAt = Instant.now();
        CodeAnalysis cloned;
        try {
            cloned = codeAnalysisService.cloneAnalysisForPr(
                    fingerprintHit.get(), project, request.getPullRequestId(),
                    request.getCommitHash(), request.getTargetBranchName(),
                    request.getSourceBranchName(), diffFingerprint);
        } catch (RuntimeException error) {
            emitStageTelemetry(consumer, "persistence", "java_analysis_cache", "failed",
                    persistenceStartedAt, 0, "analysis_persistence_failed");
            throw error;
        }
        int cachedIssues = telemetryIssueCount(cloned);
        emitStageTelemetry(consumer, "persistence", "java_analysis_cache", "complete",
                persistenceStartedAt, cachedIssues, null);
        // Persist PR-level snapshots for the source code viewer
        persistPrSnapshotsForCacheHit(pullRequest, cloned, fingerprintHit.get(), project,
                request.getCommitHash(), aiRequest.getChangedFiles());
        Instant deliveryStartedAt = Instant.now();
        try {
            publishAnalysisResults(
                    policyPlan,
                    consumer,
                    deliveryStartedAt,
                    cachedIssues,
                    reportingService,
                    cloned,
                    project,
                    request.getPullRequestId(),
                    pullRequest.getId(),
                    request.getPlaceholderCommentId(),
                    request.getCommitHash());
        } catch (IOException e) {
            emitStageTelemetry(consumer, "delivery", "java_vcs_reporting", "failed",
                    deliveryStartedAt, cachedIssues, "vcs_delivery_failed");
            log.error("Failed to post fingerprint-cached results to VCS: {}", e.getMessage(), e);
        }
        return true;
    }

    /** Describes which cache layer produced a hit. */
    protected enum CacheHitType { NONE, EXACT, COMMIT_HASH }

    protected CacheHitType postAnalysisCacheIfExist(
            Project project,
            PullRequest pullRequest,
            String commitHash,
            Long prId,
            VcsReportingService reportingService,
            String placeholderCommentId,
            String targetBranch,
            String sourceBranch
    ) {
        return postAnalysisCacheIfExist(
                project,
                pullRequest,
                commitHash,
                prId,
                reportingService,
                placeholderCommentId,
                targetBranch,
                sourceBranch,
                event -> { },
                null);
    }

    protected CacheHitType postAnalysisCacheIfExist(
            Project project,
            PullRequest pullRequest,
            String commitHash,
            Long prId,
            VcsReportingService reportingService,
            String placeholderCommentId,
            String targetBranch,
            String sourceBranch,
            EventConsumer consumer
    ) {
        return postAnalysisCacheIfExist(
                project,
                pullRequest,
                commitHash,
                prId,
                reportingService,
                placeholderCommentId,
                targetBranch,
                sourceBranch,
                consumer,
                null);
    }

    private CacheHitType postAnalysisCacheIfExist(
            Project project,
            PullRequest pullRequest,
            String commitHash,
            Long prId,
            VcsReportingService reportingService,
            String placeholderCommentId,
            String targetBranch,
            String sourceBranch,
            EventConsumer consumer,
            FrozenExecutionPlan policyPlan
    ) {
        Optional<CodeAnalysis> cachedAnalysis = codeAnalysisService.getCodeAnalysisCache(
                project.getId(),
                commitHash,
                prId);

        // Get analysis cache by PR ID and commit hash
        if (cachedAnalysis.isPresent()) {
            Instant deliveryStartedAt = Instant.now();
            int cachedIssues = telemetryIssueCount(cachedAnalysis.get());
            try {
                publishAnalysisResults(
                        policyPlan,
                        consumer,
                        deliveryStartedAt,
                        cachedIssues,
                        reportingService,
                        cachedAnalysis.get(),
                        project,
                        prId,
                        pullRequest.getId(),
                        placeholderCommentId,
                        commitHash);
            } catch (IOException e) {
                emitStageTelemetry(consumer, "delivery", "java_vcs_reporting", "failed",
                        deliveryStartedAt, cachedIssues, "vcs_delivery_failed");
                log.error("Failed to post cached analysis results to VCS: {}", e.getMessage(), e);
            }
            return CacheHitType.EXACT;
        }

        // Get analysis cache by commit hash (any PR ID)
        Optional<CodeAnalysis> commitHashHit = codeAnalysisService.getAnalysisByCommitHash(
                project.getId(), commitHash);
        if (commitHashHit.isPresent()) {
            log.info("Commit-hash cache hit for project={}, commit={} (source PR={}). Cloning for PR={}.",
                    project.getId(), commitHash,
                    commitHashHit.get().getPrNumber(), prId
            );
            Instant persistenceStartedAt = Instant.now();
            CodeAnalysis cloned;
            try {
                cloned = codeAnalysisService.cloneAnalysisForPr(
                        commitHashHit.get(), project, prId,
                        commitHash, targetBranch,
                        sourceBranch, commitHashHit.get().getDiffFingerprint());
            } catch (RuntimeException error) {
                emitStageTelemetry(consumer, "persistence", "java_analysis_cache", "failed",
                        persistenceStartedAt, 0, "analysis_persistence_failed");
                throw error;
            }
            int cachedIssues = telemetryIssueCount(cloned);
            emitStageTelemetry(consumer, "persistence", "java_analysis_cache", "complete",
                    persistenceStartedAt, cachedIssues, null);
            // Persist PR-level snapshots for the source code viewer
            persistPrSnapshotsForCacheHit(pullRequest, cloned, commitHashHit.get(), project,
                    commitHash, null);
            Instant deliveryStartedAt = Instant.now();
            try {
                publishAnalysisResults(
                        policyPlan,
                        consumer,
                        deliveryStartedAt,
                        cachedIssues,
                        reportingService,
                        cloned,
                        project,
                        prId,
                        pullRequest.getId(),
                        placeholderCommentId,
                        commitHash);
            } catch (IOException e) {
                emitStageTelemetry(consumer, "delivery", "java_vcs_reporting", "failed",
                        deliveryStartedAt, cachedIssues, "vcs_delivery_failed");
                log.error("Failed to post commit-hash cached results to VCS: {}", e.getMessage(), e);
            }
            return CacheHitType.COMMIT_HASH;
        }
        return CacheHitType.NONE;
    }

    private boolean publishAnalysisResults(
            FrozenExecutionPlan policyPlan,
            EventConsumer consumer,
            Instant deliveryStartedAt,
            int issues,
            VcsReportingService reportingService,
            CodeAnalysis analysis,
            Project project,
            Long pullRequestId,
            Long platformPullRequestId,
            String placeholderCommentId,
            String headRevision) throws IOException {
        if (policyPlan != null && executionPolicyRuntime != null) {
            EVcsProvider provider = ProjectVcsInfoRetriever.getVcsProvider(project);
            PublicationReservation reservation = executionPolicyRuntime.publicationFence().reserve(
                    policyPlan.primary(),
                    PublicationKey.forPullRequest(
                            provider.name().toLowerCase(java.util.Locale.ROOT),
                            project.getId(),
                            pullRequestId,
                            headRevision.toLowerCase(java.util.Locale.ROOT)));
            if (reservation != PublicationReservation.RESERVED) {
                String reason = reservation == PublicationReservation.SHADOW_DENIED
                        ? "shadow_publication_blocked"
                        : "duplicate_publication_blocked";
                emitStageTelemetry(
                        consumer,
                        "delivery",
                        "java_vcs_reporting",
                        "skipped",
                        deliveryStartedAt,
                        issues,
                        reason);
                return false;
            }
        }
        reportingService.postAnalysisResults(
                analysis,
                project,
                pullRequestId,
                platformPullRequestId,
                placeholderCommentId);
        emitStageTelemetry(
                consumer,
                "delivery",
                "java_vcs_reporting",
                "complete",
                deliveryStartedAt,
                issues,
                null);
        return true;
    }

    /**
     * After successful PR analysis, record the source branch HEAD commit
     * as analyzed in the analyzed_commit table.
     *
     * @param project      the project
     * @param sourceBranch the PR source branch (where the commits live)
     * @param commitHash   the HEAD commit of the source branch
     * @param analysis     the CodeAnalysis to link, or null for cache-hit scenarios
     */
    private void markPrCommitsAnalyzed(Project project, String sourceBranch, String commitHash, CodeAnalysis analysis) {
        try {
            if (commitHash == null) return;

            // Record the PR's HEAD commit as analyzed
            analyzedCommitService.recordPrCommitsAnalyzed(
                    project, List.of(commitHash), analysis);

            log.info("Recorded PR commit {} as analyzed (branch={}, analysis={})",
                    commitHash.substring(0, Math.min(7, commitHash.length())),
                    sourceBranch,
                    analysis != null ? analysis.getId() : "none");
        } catch (Exception e) {
            log.warn("Failed to record PR commit as analyzed (non-critical): branch={}, error={}",
                    sourceBranch, e.getMessage());
        }
    }

    /**
     * Ensures RAG index is up-to-date for the PR target branch.
     * <p>
     * For PRs targeting the main branch:
     * - Checks if the main RAG index commit matches the current target branch HEAD
     * - If outdated, performs incremental update before analysis
     * <p>
     * For PRs targeting non-main branches with multi-branch enabled:
     * - First ensures the main index is up to date
     * - Then ensures branch index exists and is up to date for the target branch
     * <p>
     * This ensures analysis always uses the most current codebase context.
     */
    private String ensureRagIndexForTargetBranch(Project project, String targetBranch, EventConsumer consumer) {
        if (ragOperationsService == null) {
            log.debug("RagOperationsService not available - skipping RAG index check for target branch");
            return "rag_service_unavailable";
        }

        try {
            boolean ready = ragOperationsService.ensureRagIndexUpToDate(
                    project,
                    targetBranch,
                    consumer::accept);
            if (ready) {
                log.info("RAG index ensured up-to-date for PR target branch: project={}, branch={}",
                        project.getId(), targetBranch);
                return null;
            }
            return "rag_index_not_ready";
        } catch (Exception e) {
            log.warn(
                    "Failed to ensure RAG index up-to-date for target branch (non-critical): errorType={}",
                    e.getClass().getSimpleName());
            return "rag_index_refresh_failed";
        }
    }

    private String resolveIndexVersion(Project project, String targetBranch) {
        if (ragOperationsService == null) {
            return "rag-service-unavailable";
        }
        try {
            String version = ragOperationsService.getIndexVersion(project, targetBranch);
            return version == null || !EXACT_INDEX_VERSION.matcher(version).matches()
                    ? "rag-version-unavailable"
                    : version;
        } catch (RuntimeException error) {
            log.warn("RAG index version lookup failed: {}", error.getClass().getSimpleName());
            return "rag-version-unavailable";
        }
    }

    private StageObservation terminalStage(
            String stage,
            String producer,
            String outcome,
            Instant startedAt,
            int itemCount,
            String reasonCode) {
        return new StageObservation(
                stage,
                producer,
                outcome,
                Math.max(0L, Duration.between(startedAt, Instant.now()).toMillis()),
                Math.max(0, itemCount),
                reasonCode);
    }

    @SuppressWarnings("unchecked")
    private Map<String, Object> finalizePipelineTelemetry(
            Map<String, Object> aiResponse,
            EventConsumer consumer,
            Instant executionStartedAt,
            List<StageObservation> javaStages) {
        if (aiResponse == null) {
            return null;
        }
        Object rawTelemetry = aiResponse.get("telemetry");
        if (!(rawTelemetry instanceof Map<?, ?> rawMap)) {
            emitTerminalUnavailable(consumer, "python_snapshot_unavailable");
            return aiResponse;
        }
        Map<String, Object> finalized;
        try {
            Map<String, Object> snapshot = new LinkedHashMap<>();
            rawMap.forEach((key, value) -> snapshot.put(String.valueOf(key), value));
            finalized = PipelineTelemetryFinalizer.finalizeDocument(
                    snapshot,
                    javaStages,
                    Math.max(0L, Duration.between(executionStartedAt, Instant.now()).toMillis()));
        } catch (RuntimeException error) {
            log.warn("Terminal telemetry finalization rejected: {}", error.getClass().getSimpleName());
            emitTerminalUnavailable(consumer, "terminal_contract_rejected");
            return aiResponse;
        }

        Map<String, Object> result = new LinkedHashMap<>(aiResponse);
        result.put("telemetry", finalized);
        Map<String, Object> trace = (Map<String, Object>) finalized.get("trace");
        Map<String, Object> event = new LinkedHashMap<>();
        event.put("type", "telemetry");
        event.put("state", "emitted");
        event.put("outcome", trace.get("outcome"));
        event.put("reason", trace.get("reason"));
        event.put("telemetry", finalized);
        try {
            consumer.accept(Collections.unmodifiableMap(event));
        } catch (RuntimeException error) {
            // The finalized analysis artifact remains valid even when its
            // observational event stream is unavailable.
            log.warn("Terminal telemetry event emission failed: {}", error.getClass().getSimpleName());
        }
        return result;
    }

    private void emitTerminalUnavailable(EventConsumer consumer, String reason) {
        try {
            consumer.accept(Map.of(
                    "type", "telemetry",
                    "state", "not_emitted",
                    "reason", reason));
        } catch (RuntimeException error) {
            log.warn("Terminal telemetry diagnostic emission failed: {}", error.getClass().getSimpleName());
        }
    }

    private Map<String, Object> attachFinalizedTelemetry(
            Map<String, Object> result,
            Map<String, Object> finalizedAiResponse) {
        if (finalizedAiResponse == null || !finalizedAiResponse.containsKey("telemetry")) {
            return result;
        }
        Map<String, Object> withTelemetry = new LinkedHashMap<>(result);
        withTelemetry.put("telemetry", finalizedAiResponse.get("telemetry"));
        return withTelemetry;
    }

    /**
     * Emits a bounded operational stage observation. Source, prompt, credential,
     * customer, revision, and project identifiers are deliberately absent; those
     * high-cardinality values belong only in the execution trace artifact.
     */
    private void emitStageTelemetry(EventConsumer consumer,
                                    String stage,
                                    String producer,
                                    String outcome,
                                    Instant startedAt,
                                    int itemCount,
                                    String reasonCode) {
        try {
            Map<String, Object> event = new HashMap<>();
            event.put("type", "telemetry");
            event.put("schemaVersion", 1);
            event.put("stage", stage);
            event.put("producer", producer);
            event.put("outcome", outcome);
            event.put("durationMs", Math.max(0L, Duration.between(startedAt, Instant.now()).toMillis()));
            event.put("itemCount", Math.max(0, itemCount));
            if (reasonCode != null) {
                event.put("reasonCode", reasonCode);
            }
            consumer.accept(Collections.unmodifiableMap(event));
        } catch (Exception error) {
            // Telemetry is observational and must never replace the analysis result.
            log.warn("Stage telemetry emission failed: {}", error.getClass().getSimpleName());
        }
    }

    private int telemetryIssueCount(CodeAnalysis analysis) {
        try {
            if (analysis == null) {
                return 0;
            }
            var issues = analysis.getIssues();
            return issues != null ? issues.size() : 0;
        } catch (RuntimeException error) {
            return 0;
        }
    }

    /**
     * Publishes an AnalysisStartedEvent for PR analysis.
     */
    private void publishAnalysisStartedEvent(Project project, PrProcessRequest request, String correlationId) {
        if (eventPublisher == null) {
            return;
        }
        try {
            AnalysisStartedEvent event = new AnalysisStartedEvent(
                    this,
                    correlationId,
                    project.getId(),
                    project.getName(),
                    AnalysisStartedEvent.AnalysisType.PULL_REQUEST,
                    request.getSourceBranchName(),
                    null // jobId not available at this level
            );
            eventPublisher.publishEvent(event);
            log.debug("Published AnalysisStartedEvent for PR analysis: project={}, pr={}",
                    project.getId(), request.getPullRequestId());
        } catch (Exception e) {
            log.warn("Failed to publish AnalysisStartedEvent: {}", e.getMessage());
        }
    }

    /**
     * Publishes an AnalysisCompletedEvent for PR analysis.
     */
    private void publishAnalysisCompletedEvent(Project project, PrProcessRequest request,
                                               String correlationId, Instant startTime,
                                               AnalysisCompletedEvent.CompletionStatus status, int issuesFound,
                                               int filesAnalyzed, String errorMessage) {
        if (eventPublisher == null) {
            return;
        }
        try {
            Duration duration = Duration.between(startTime, Instant.now());
            Map<String, Object> metrics = new HashMap<>();
            metrics.put("prNumber", request.getPullRequestId());
            metrics.put("targetBranch", request.getTargetBranchName());
            metrics.put("sourceBranch", request.getSourceBranchName());
            metrics.put("commitHash", request.getCommitHash());
            if (request.getPrTitle() != null) {
                metrics.put("prTitle", request.getPrTitle());
            }
            if (request.getPrDescription() != null) {
                metrics.put("prDescription", request.getPrDescription());
            }

            AnalysisCompletedEvent event = new AnalysisCompletedEvent(
                    this,
                    correlationId,
                    project.getId(),
                    null, // jobId not available at this level
                    status,
                    duration,
                    issuesFound,
                    filesAnalyzed,
                    errorMessage,
                    metrics,
                    project.getWorkspace().getName(),
                    project.getNamespace(),
                    request.getPullRequestId());
            eventPublisher.publishEvent(event);
            log.debug("Published AnalysisCompletedEvent for PR analysis: project={}, pr={}, status={}, duration={}ms",
                    project.getId(), request.getPullRequestId(), status, duration.toMillis());
        } catch (Exception e) {
            log.warn("Failed to publish AnalysisCompletedEvent: {}", e.getMessage());
        }
    }
}
