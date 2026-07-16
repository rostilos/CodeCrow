package org.rostilos.codecrow.analysisengine.processor.analysis;

import org.rostilos.codecrow.analysisengine.util.ProjectVcsInfoRetriever;
import org.rostilos.codecrow.core.model.analysis.AnalysisLockType;
import org.rostilos.codecrow.core.model.ai.AIConnection;
import org.rostilos.codecrow.core.model.codeanalysis.CodeAnalysis;
import org.rostilos.codecrow.core.model.project.Project;
import org.rostilos.codecrow.core.model.project.ProjectAiConnectionBinding;
import org.rostilos.codecrow.core.model.project.config.AnalysisLimitsConfig;
import org.rostilos.codecrow.core.model.project.config.ProjectConfig;
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
import org.rostilos.codecrow.analysisengine.execution.ExecutionManifestService;
import org.rostilos.codecrow.analysisengine.execution.ExecutionInputArtifactBundle;
import org.rostilos.codecrow.analysisengine.execution.ImmutableExecutionManifest;
import org.rostilos.codecrow.analysisengine.delivery.ReviewDeliveryIntent;
import org.rostilos.codecrow.analysisengine.delivery.ReviewDeliveryHead;
import org.rostilos.codecrow.analysisengine.delivery.ReviewDeliveryOutcome;
import org.rostilos.codecrow.analysisengine.delivery.ReviewProviderEffectIdentity;
import org.rostilos.codecrow.analysisengine.delivery.ReviewDeliveryService;
import org.rostilos.codecrow.analysisengine.delivery.ReviewDeliveryState;
import org.rostilos.codecrow.analysisengine.delivery.ReviewDeliveryTruth;
import org.rostilos.codecrow.analysisengine.coverage.CoverageAnalysisState;
import org.rostilos.codecrow.analysisengine.coverage.CoverageAnchorState;
import org.rostilos.codecrow.analysisengine.coverage.CoverageCounts;
import org.rostilos.codecrow.analysisengine.coverage.CoverageDisposition;
import org.rostilos.codecrow.analysisengine.coverage.CoverageLedgerService;
import org.rostilos.codecrow.analysisengine.coverage.CoverageLedgerSnapshot;
import org.rostilos.codecrow.analysisengine.coverage.CoverageReceipt;
import org.rostilos.codecrow.analysisengine.coverage.CoverageWorkPlan;
import org.rostilos.codecrow.analysisengine.service.AnalysisLockService;
import org.rostilos.codecrow.analysisengine.service.PullRequestService;
import org.rostilos.codecrow.analysisapi.rag.RagOperationsService;
import org.rostilos.codecrow.commitgraph.service.AnalyzedCommitService;
import org.rostilos.codecrow.analysisengine.service.vcs.VcsAiClientService;
import org.rostilos.codecrow.analysisengine.service.vcs.VcsReportingService;
import org.rostilos.codecrow.analysisengine.service.vcs.VcsServiceFactory;
import org.rostilos.codecrow.analysisengine.aiclient.AiAnalysisClient;
import org.rostilos.codecrow.analysisengine.policy.ExecutionLifecycle;
import org.rostilos.codecrow.analysisengine.policy.ExecutionPolicyConfig;
import org.rostilos.codecrow.analysisengine.policy.ExecutionPolicyControlPlane;
import org.rostilos.codecrow.analysisengine.policy.ExecutionPolicyRuntime;
import org.rostilos.codecrow.analysisengine.policy.FrozenExecutionPlan;
import org.rostilos.codecrow.analysisengine.policy.LatestHeadRegistration;
import org.rostilos.codecrow.analysisengine.policy.PublicationFence;
import org.rostilos.codecrow.analysisengine.policy.PublicationKey;
import org.rostilos.codecrow.analysisengine.policy.PublicationReservation;
import org.rostilos.codecrow.analysisengine.policy.StableRolloutKey;
import org.rostilos.codecrow.analysisengine.service.vcs.ExactHeadAdmission;
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
import java.nio.charset.StandardCharsets;
import java.security.GeneralSecurityException;
import java.time.Duration;
import java.time.Instant;
import java.util.HashMap;
import java.util.LinkedHashMap;
import java.util.List;
import java.util.Locale;
import java.util.Map;
import java.util.Optional;
import java.util.OptionalLong;
import java.util.Collections;
import java.util.LinkedHashSet;
import java.util.Set;
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
    private static final String CANDIDATE_PROMPT_CONTRACT_VERSION =
            "candidate-review-prompts-v1";
    private static final String CANDIDATE_INDEX_IDENTITY = "rag-disabled";
    private static final String CANDIDATE_ARTIFACT_PRODUCER =
            "java-vcs-acquisition";
    private static final String CANDIDATE_ARTIFACT_PRODUCER_VERSION =
            "analysis-engine-v1";
    private static final Pattern EXACT_INDEX_VERSION = Pattern.compile(
            "(?:rag-disabled|rag-commit-(?:[0-9a-f]{40}|[0-9a-f]{64}))");

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
    private final ExecutionManifestService executionManifestService;
    private final CoverageLedgerService coverageLedgerService;
    private ReviewDeliveryService reviewDeliveryService;

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
                null,
                null,
                null);
    }

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
                executionPolicyRuntime,
                null,
                null);
    }

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
            @Autowired(required = false) ExecutionPolicyRuntime executionPolicyRuntime,
            @Autowired(required = false) ExecutionManifestService executionManifestService
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
                executionPolicyRuntime,
                executionManifestService,
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
            @Autowired(required = false) ExecutionPolicyRuntime executionPolicyRuntime,
            @Autowired(required = false) ExecutionManifestService executionManifestService,
            @Autowired(required = false) CoverageLedgerService coverageLedgerService
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
        this.executionManifestService = executionManifestService;
        this.coverageLedgerService = coverageLedgerService;
    }

    /** Injected by the pipeline service; candidate analysis fails closed without it. */
    @Autowired(required = false)
    public void setReviewDeliveryService(ReviewDeliveryService reviewDeliveryService) {
        this.reviewDeliveryService = reviewDeliveryService;
    }

    private ReviewDeliveryService requireCandidateDeliveryService() {
        if (reviewDeliveryService == null) {
            throw new IllegalStateException(
                    "candidate analysis requires durable delivery service");
        }
        return reviewDeliveryService;
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
        ExecutionPolicyConfig policyConfig = executionPolicyRuntime == null
                ? null
                : java.util.Objects.requireNonNull(
                        executionPolicyRuntime.currentConfig(),
                        "execution policy config is required");
        StableRolloutKey stableRolloutKey = policyConfig == null
                ? null
                : stableRolloutKey(project, request);
        boolean candidatePath = policyConfig != null
                && ExecutionPolicyControlPlane.selectsCandidatePrimary(
                        stableRolloutKey, policyConfig);
        ReviewDeliveryService candidateDeliveryService = candidatePath
                ? requireCandidateDeliveryService()
                : null;
        FrozenExecutionPlan policyPlan = candidatePath
                ? null
                : freezePolicyPlan(
                        project, request, policyConfig, stableRolloutKey);
        ExecutionLifecycle policyLifecycle = policyPlan == null
                ? null
                : new ExecutionLifecycle(policyPlan.primary());
        ImmutableExecutionManifest executionManifest = null;
        CoverageWorkPlan coverageWorkPlan = null;
        CoverageLedgerSnapshot terminalCoverage = null;
        ExecutionEventBinding executionEventBinding = null;
        PublicationKey latestHeadPublicationKey = null;
        long latestHeadGeneration = 0L;
        boolean latestHeadRegistered = false;
        EVcsProvider provider = null;
        VcsAiClientService aiClientService = null;
        Instant acquisitionStartedAt = null;
        List<AiAnalysisRequest> aiRequests = null;
        if (!candidatePath) {
            emitPolicySelection(consumer, policyPlan, null);
            publishAnalysisStartedEvent(project, request, correlationId, null);
        } else {
            try {
                provider = ProjectVcsInfoRetriever.getVcsProvider(project);
                aiClientService = vcsServiceFactory.getAiClientService(provider);
                latestHeadPublicationKey = PublicationKey.forPullRequest(
                        provider.name().toLowerCase(Locale.ROOT),
                        project.getId(),
                        request.getPullRequestId(),
                        request.getCommitHash().toLowerCase(Locale.ROOT));
                String admissionId = "admission:" + java.util.UUID.randomUUID();
                PublicationFence latestHeadFence = requirePublicationFence();
                latestHeadGeneration = latestHeadFence.claimLatestHeadGeneration(
                        admissionId, latestHeadPublicationKey);
                long claimedGeneration = latestHeadGeneration;
                PublicationKey claimedPublicationKey = latestHeadPublicationKey;
                ExactHeadAdmission exactHeadAdmission = verifiedHeadRevision -> {
                    if (!claimedPublicationKey.headRevision().equals(
                            verifiedHeadRevision)) {
                        throw new IllegalStateException(
                                "provider-verified head conflicts with accepted candidate head");
                    }
                    OptionalLong installed = latestHeadFence
                            .findLatestHeadGeneration(claimedPublicationKey);
                    if (installed.isPresent()
                            && installed.getAsLong() > claimedGeneration) {
                        throw new IllegalStateException(
                                "candidate head was superseded before exact input acquisition");
                    }
                };
                acquisitionStartedAt = Instant.now();
                aiRequests = acquireAiRequests(
                        aiClientService,
                        project,
                        request,
                        Optional.empty(),
                        List.of(),
                        true,
                        consumer,
                        acquisitionStartedAt,
                        exactHeadAdmission);
                if (aiRequests == null || aiRequests.size() != 1
                        || aiRequests.get(0) == null) {
                    throw new IllegalStateException(
                            "exact acquisition must return one manifest-bound request");
                }
                AiAnalysisRequest exactRequest = aiRequests.get(0);
                String executionId = candidateExecutionIdentity(
                        project,
                        request,
                        policyConfig,
                        exactRequest,
                        CANDIDATE_INDEX_IDENTITY);
                policyPlan = executionPolicyRuntime.freeze(
                        executionId, stableRolloutKey, policyConfig);
                if (!isCandidatePath(policyPlan)) {
                    throw new IllegalStateException(
                            "candidate policy preview conflicts with the frozen execution plan");
                }
                policyLifecycle = new ExecutionLifecycle(policyPlan.primary());
                executionManifest = persistCandidateManifest(
                        exactRequest, request, policyPlan);
                executionManifest = requireReloadedCandidateManifest(executionManifest);
                executionEventBinding = ExecutionEventBinding.require(
                        policyPlan, executionManifest);
                LatestHeadRegistration headRegistration = registerLatestHead(
                        policyPlan,
                        latestHeadPublicationKey,
                        latestHeadGeneration);
                latestHeadRegistered = true;
                if (headRegistration == LatestHeadRegistration.SUPERSEDED) {
                    return supersededResult(consumer, executionEventBinding);
                }
                if (project.getWorkspace() == null
                        || project.getWorkspace().getId() == null) {
                    throw new IllegalStateException(
                            "durable delivery requires a tenant identity");
                }
                ReviewDeliveryHead proposedDeliveryHead =
                        new ReviewDeliveryHead(
                                provider.name().toLowerCase(Locale.ROOT),
                                project.getWorkspace().getId(),
                                project.getId(),
                                executionManifest.repositoryId(),
                                request.getPullRequestId(),
                                executionManifest.executionId(),
                                executionManifest.headSha(),
                                latestHeadGeneration);
                ReviewDeliveryHead durableDeliveryHead =
                        candidateDeliveryService.registerCurrentHead(
                                proposedDeliveryHead);
                if (!proposedDeliveryHead.equals(durableDeliveryHead)) {
                    return supersededResult(consumer, executionEventBinding);
                }
            } catch (GeneralSecurityException | RuntimeException error) {
                failPolicyLifecycle(policyLifecycle);
                throw error;
            }
        }

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
                    candidatePath ? ignored -> { } : consumer::accept);

            if (acquiredLock.isEmpty()) {
                String message = String.format(
                        "Failed to acquire lock after %d minutes for project=%s, PR=%d, branch=%s. Another analysis is still in progress.",
                        analysisLockService.getLockWaitTimeoutMinutes(),
                        project.getId(),
                        request.getPullRequestId(),
                        request.getSourceBranchName());
                log.warn(message);

                // Publish failed event due to lock timeout
                if (!candidatePath) {
                    publishAnalysisCompletedEvent(project, request, correlationId, startTime,
                            AnalysisCompletedEvent.CompletionStatus.FAILED, 0, 0,
                            "Lock acquisition timeout", null);
                }
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
            if (!candidatePath && cancelRequested(policyLifecycle)) {
                return cancelledResult(
                        project, request, correlationId, startTime, consumer, null);
            }
            if (!candidatePath) {
                provider = ProjectVcsInfoRetriever.getVcsProvider(project);
                aiClientService = vcsServiceFactory.getAiClientService(provider);
            }
            VcsReportingService reportingService = vcsServiceFactory.getReportingService(provider);
            PullRequest pullRequest = candidatePath
                    ? null
                    : pullRequestService.createOrUpdatePullRequest(
                            request.getProjectId(),
                            request.getPullRequestId(),
                            request.getCommitHash(),
                            request.getSourceBranchName(),
                            request.getTargetBranchName(),
                            project);

            // Final CodeAnalysis rows are historical evidence, not reusable producer
            // output. Every execution reaches current acquisition and model work;
            // only immutable input artifacts may be cached independently.
            List<CodeAnalysis> allPrAnalyses = candidatePath
                    ? List.of()
                    : codeAnalysisService.getAllPrAnalyses(
                            project.getId(), request.getPullRequestId());

            Optional<CodeAnalysis> previousAnalysis = candidatePath || allPrAnalyses.isEmpty()
                    ? Optional.empty()
                    : Optional.of(allPrAnalyses.get(0));

            if (candidatePath) {
                if (coverageLedgerService == null) {
                    throw new IllegalStateException(
                            "candidate execution requires durable coverage accounting");
                }
                AiAnalysisRequest exactRequest = aiRequests.get(0);
                coverageWorkPlan = coverageLedgerService.initializeOrVerify(
                        executionManifest,
                        exactRequest.getRawDiff(),
                        eligibleCoveragePaths(exactRequest));
                terminalCoverage = coverageLedgerService.requireSnapshot(
                        executionManifest.executionId());
                if (latestHeadRegistered
                        && !isLatestHead(policyPlan, latestHeadPublicationKey)) {
                    supersedeCoverage(executionManifest);
                    return supersededResult(consumer, executionEventBinding);
                }
                // The exact comparison is the sole pre-manifest input read.
                // Mutable PR state is created/updated only after durable
                // manifest create-or-load and restart verification succeed.
                pullRequest = pullRequestService.createOrUpdatePullRequest(
                        request.getProjectId(),
                        request.getPullRequestId(),
                        request.getCommitHash(),
                        request.getSourceBranchName(),
                        request.getTargetBranchName(),
                        project);
                emitPolicySelection(consumer, policyPlan, executionEventBinding);
                publishAnalysisStartedEvent(
                        project, request, correlationId, executionEventBinding);

                if (terminalCoverage != null
                        && terminalCoverage.analysisState() == CoverageAnalysisState.EMPTY) {
                    return noCoverageAnchors(
                            project,
                            request,
                            consumer,
                            correlationId,
                            startTime,
                            acquisitionStartedAt,
                            policyLifecycle,
                            executionEventBinding,
                            terminalCoverage);
                }
                if (cancelRequested(policyLifecycle)) {
                    failPendingCoverage(executionManifest, "analysis_cancelled");
                    return cancelledResult(
                            project,
                            request,
                            correlationId,
                            startTime,
                            consumer,
                            executionEventBinding);
                }
            }

            Instant retrievalStartedAt = Instant.now();
            String retrievalReason;
            String indexVersion;
            if (candidatePath) {
                // P1-01 has no manifest-bound index-generation identity. Do not
                // consult the mutable project/target-branch RAG state for a v1
                // execution; P2-06 owns re-enabling retrieval once an exact
                // generation is part of the immutable contract.
                indexVersion = CANDIDATE_INDEX_IDENTITY;
                retrievalReason = "rag_disabled";
            } else {
                // Legacy behavior: refresh the mutable target branch before analysis.
                retrievalReason = ensureRagIndexForTargetBranch(
                        project, request.getTargetBranchName(), consumer);
                indexVersion = resolveIndexVersion(
                        project, request.getTargetBranchName());
            }
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
                    retrievalReason,
                    executionEventBinding);
            StageObservation retrievalTerminalStage = terminalStage(
                    "retrieval",
                    "java_rag_index",
                    retrievalOutcome,
                    retrievalStartedAt,
                    0,
                    retrievalReason);

            if (!candidatePath) {
                acquisitionStartedAt = Instant.now();
                aiRequests = acquireAiRequests(
                        aiClientService,
                        project,
                        request,
                        previousAnalysis,
                        allPrAnalyses,
                        false,
                        consumer,
                        acquisitionStartedAt);
            }

            if (aiRequests == null || aiRequests.isEmpty()) {
                return noAnalyzableChanges(
                        project,
                        request,
                        consumer,
                        correlationId,
                        startTime,
                        acquisitionStartedAt,
                        policyLifecycle,
                        null);
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
                    null,
                    executionEventBinding);
            StageObservation acquisitionTerminalStage = terminalStage(
                    "acquisition",
                    "java_vcs_diff",
                    "complete",
                    acquisitionStartedAt,
                    changedFiles.size(),
                    null);
            String diffFingerprint = DiffFingerprintUtil.compute(aiRequest.getRawDiff());

            if (cancelRequested(policyLifecycle)) {
                failPendingCoverage(executionManifest, "analysis_cancelled");
                return cancelledResult(
                        project,
                        request,
                        correlationId,
                        startTime,
                        consumer,
                        executionEventBinding);
            }
            if (candidatePath
                    && latestHeadRegistered
                    && !isLatestHead(policyPlan, latestHeadPublicationKey)) {
                supersedeCoverage(executionManifest);
                return supersededResult(consumer, executionEventBinding);
            }
            ExecutionEventBinding activeEventBinding = executionEventBinding;
            Consumer<Map<String, Object>> aiEventConsumer = event -> {
                Map<String, Object> forwarded = activeEventBinding == null
                        ? event
                        : activeEventBinding.requireProducerBound(event);
                try {
                    log.debug("Received event from AI client: type={}", event.get("type"));
                    consumer.accept(forwarded);
                    log.debug("Event forwarded to consumer successfully");
                } catch (RuntimeException ex) {
                    if (activeEventBinding != null) {
                        throw ex;
                    }
                    log.error("Event consumer failed: {}", ex.getMessage(), ex);
                }
            };
            Map<String, Object> aiResponse = performAiAnalysis(
                    aiRequest,
                    aiEventConsumer,
                    policyPlan,
                    indexVersion,
                    executionManifest,
                    coverageWorkPlan);

            if (candidatePath
                    && latestHeadRegistered
                    && !isLatestHead(policyPlan, latestHeadPublicationKey)) {
                supersedeCoverage(executionManifest);
                return supersededResult(consumer, executionEventBinding);
            }

            if (candidatePath && "superseded".equals(aiResponse.get("status"))) {
                if (!"latest_head_advanced".equals(aiResponse.get("reason"))) {
                    throw new IOException(
                            "Candidate supersession response has an invalid reason");
                }
                if (!latestHeadRegistered
                        || isLatestHead(policyPlan, latestHeadPublicationKey)) {
                    throw new IOException(
                            "Candidate worker reported supersession without an advanced latest-head fence");
                }
                supersedeCoverage(executionManifest);
                return supersededResult(
                        consumer,
                        executionEventBinding,
                        aiResponse.get("computeState"));
            }

            if (coverageWorkPlan != null) {
                CoverageReceipt producerReceipt = coverageReceipt(aiResponse);
                terminalCoverage = coverageLedgerService.reconcileProducer(
                        executionManifest, producerReceipt);
                aiResponse = attachCoverageTruth(aiResponse, terminalCoverage);
                if (terminalCoverage.analysisState() == CoverageAnalysisState.FAILED) {
                    throw new IOException(
                            "AI producer failed to examine any mandatory coverage anchor");
                }
            }

            Map<String, String> fileContents = new HashMap<>(
                    extractFileContents(aiRequest));
            boolean candidatePublicationWithheld = false;
            String nonPublicationReason = null;
            boolean analysisPartial = terminalCoverage != null
                    && terminalCoverage.analysisState()
                    == CoverageAnalysisState.PARTIAL;

            if (cancelRequested(policyLifecycle)) {
                failPendingCoverage(executionManifest, "analysis_cancelled");
                Map<String, Object> cancelled = cancelledResult(
                        project,
                        request,
                        correlationId,
                        startTime,
                        consumer,
                        executionEventBinding);
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
                                        "kill_switch")),
                        executionManifest,
                        indexVersion);
                return attachFinalizedTelemetry(cancelled, finalized);
            }

            // === Extract file contents from enrichment data for line hash computation ===
            java.util.Set<String> allChangedFiles = new java.util.HashSet<>(changedFiles);
            if (aiRequest.getDeletedFiles() != null) {
                allChangedFiles.addAll(aiRequest.getDeletedFiles());
            }

            // === VCS fallback: when enrichment data is empty (disabled, failed, or
            // provider-specific),
            // fetch file contents directly from VCS to ensure source viewer always has data
            // ===
            if (!candidatePath && fileContents.isEmpty()) {
                log.info(
                        "Enrichment file contents empty — falling back to direct VCS file fetch for PR {} (project={})",
                        request.getPullRequestId(), project.getId());
                fileContents = fetchFileContentsFromVcs(project, new java.util.ArrayList<>(allChangedFiles),
                        request.getCommitHash());
            }

            if (candidatePath
                    && latestHeadRegistered
                    && !isLatestHead(policyPlan, latestHeadPublicationKey)) {
                supersedeCoverage(executionManifest);
                return supersededResult(consumer, executionEventBinding);
            }

            Instant persistenceStartedAt = Instant.now();
            CodeAnalysis newAnalysis;
            try {
                String taskId = taskContextValue(
                        aiRequest, "task_key", "taskKey", "key");
                String taskSummary = taskContextValue(
                        aiRequest, "task_summary", "taskSummary", "summary");
                if (candidatePath) {
                    newAnalysis = codeAnalysisService
                            .createCandidateAnalysisFromAiResponse(
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
                                    taskId,
                                    taskSummary,
                                    executionManifest.executionId(),
                                    executionManifest.artifactManifestDigest());
                    requireCandidateOutputBinding(newAnalysis, executionManifest);
                } else {
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
                            taskId,
                            taskSummary);
                }
            } catch (RuntimeException error) {
                emitStageTelemetry(
                        consumer,
                        "persistence",
                        "java_analysis_store",
                        "failed",
                        persistenceStartedAt,
                        0,
                        "analysis_persistence_failed",
                        executionEventBinding);
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
                                        "upstream_failed")),
                        executionManifest,
                        indexVersion);
                throw error;
            }

            int issuesFound = newAnalysis.getIssues() != null ? newAnalysis.getIssues().size() : 0;
            // Partial findings are still useful, but incomplete coverage can
            // never authorize a clean (zero-finding) publication.
            if (candidatePath && analysisPartial && issuesFound == 0) {
                candidatePublicationWithheld = true;
                nonPublicationReason = "coverage_incomplete_no_clean_claim";
            }
            emitStageTelemetry(
                    consumer,
                    "persistence",
                    "java_analysis_store",
                    "complete",
                    persistenceStartedAt,
                    issuesFound,
                    null,
                    executionEventBinding);
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

            // Delivery retries reload CodeAnalysis from PostgreSQL. Freeze any
            // post-processing applied after initial model persistence before
            // deriving the delivery truth digest, so the first attempt and a
            // restarted attempt observe byte-for-byte equivalent model truth.
            if (candidatePath) {
                newAnalysis = codeAnalysisService.saveAnalysis(newAnalysis);
            }

            // The legacy PR snapshot table is mutable by PR/path and can retain
            // an earlier analysis when content is unchanged. Candidate inputs
            // already live in immutable review_artifact rows; do not create a
            // second downstream artifact until that table has execution binding.
            if (executionManifest == null) {
                try {
                    fileSnapshotService.persistSnapshotsForPr(
                            pullRequest,
                            newAnalysis,
                            fileContents,
                            request.getCommitHash());
                } catch (Exception snapEx) {
                    log.warn("Failed to persist file snapshots (non-critical): {}", snapEx.getMessage());
                }
            }

            // === Deterministic PR issue tracking against previous iteration ===
            // The candidate queue deliberately has no manifest-bound prior
            // issue inventory. Keep legacy reconciliation out of that path so
            // it cannot import cross-execution state after model persistence.
            if (executionManifest == null) {
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
            }

            if (cancelRequested(policyLifecycle)) {
                failPendingCoverage(executionManifest, "analysis_cancelled");
                Map<String, Object> cancelled = cancelledResult(
                        project,
                        request,
                        correlationId,
                        startTime,
                        consumer,
                        executionEventBinding);
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
                                        "kill_switch")),
                        executionManifest,
                        indexVersion);
                return attachFinalizedTelemetry(cancelled, finalized);
            }

            if (candidatePath
                    && latestHeadRegistered
                    && !isLatestHead(policyPlan, latestHeadPublicationKey)) {
                supersedeCoverage(executionManifest);
                return supersededResult(consumer, executionEventBinding);
            }

            Instant deliveryStartedAt = Instant.now();
            StageObservation deliveryTerminalStage;
            try {
                boolean published;
                if (candidatePublicationWithheld) {
                    published = false;
                    emitStageTelemetry(
                            consumer,
                            "delivery",
                            "java_vcs_reporting",
                            "skipped",
                            deliveryStartedAt,
                            issuesFound,
                            nonPublicationReason,
                            executionEventBinding);
                } else {
                    published = candidatePath
                            ? deliverCandidateAnalysis(
                                    candidateDeliveryService,
                                    consumer,
                                    deliveryStartedAt,
                                    issuesFound,
                                    newAnalysis,
                                    project,
                                    request.getPullRequestId(),
                                    request.getCommitHash(),
                                    executionManifest.repositoryId(),
                                    latestHeadGeneration,
                                    executionEventBinding)
                            : publishAnalysisResults(
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
                                    request.getCommitHash(),
                                    executionEventBinding);
                }
                deliveryTerminalStage = terminalStage(
                        "delivery",
                        "java_vcs_reporting",
                        published ? "complete" : "skipped",
                        deliveryStartedAt,
                        issuesFound,
                        published ? null : candidatePublicationWithheld
                                ? nonPublicationReason
                                : "publication_fence_denied");
                if (!published
                        && candidatePath
                        && latestHeadRegistered
                        && !isLatestHead(policyPlan, latestHeadPublicationKey)) {
                    supersedeCoverage(executionManifest);
                    return supersededResult(consumer, executionEventBinding);
                }
            } catch (IOException e) {
                emitStageTelemetry(
                        consumer,
                        "delivery",
                        "java_vcs_reporting",
                        "failed",
                        deliveryStartedAt,
                        issuesFound,
                        "vcs_delivery_failed",
                        executionEventBinding);
                deliveryTerminalStage = terminalStage(
                        "delivery",
                        "java_vcs_reporting",
                        "failed",
                        deliveryStartedAt,
                        issuesFound,
                        "vcs_delivery_failed");
                log.error("Failed to post analysis results to VCS: {}", e.getMessage(), e);
                try {
                    Map<String, Object> warning = Map.of(
                            "type", "warning",
                            "message", "Analysis completed but failed to post results to VCS: " + e.getMessage());
                    consumer.accept(executionEventBinding == null
                            ? warning
                            : executionEventBinding.bindOwned(warning));
                } catch (RuntimeException eventError) {
                    log.warn("VCS delivery warning emission failed: {}",
                            eventError.getClass().getSimpleName());
                }
            }

            // === DAG: Mark PR commits as ANALYZED ===
            markPrCommitsAnalyzed(project, request.getSourceBranchName(), request.getCommitHash(), newAnalysis);

            // Publish successful completion event
            publishAnalysisCompletedEvent(project, request, correlationId, startTime,
                    (analysisPartial
                            || (terminalCoverage != null
                                    && terminalCoverage.analysisState()
                                    == CoverageAnalysisState.PARTIAL))
                            ? AnalysisCompletedEvent.CompletionStatus.PARTIAL_SUCCESS
                            : AnalysisCompletedEvent.CompletionStatus.SUCCESS,
                    issuesFound,
                    allChangedFiles.size(), null, executionEventBinding);
            completePolicyLifecycle(policyLifecycle);

            return finalizePipelineTelemetry(
                    aiResponse,
                    consumer,
                    startTime,
                    List.of(
                            acquisitionTerminalStage,
                            retrievalTerminalStage,
                            persistenceTerminalStage,
                            deliveryTerminalStage),
                    executionManifest,
                    indexVersion);
        } catch (IOException e) {
            failPendingCoverage(executionManifest, "analysis_pipeline_failed");
            failPolicyLifecycle(policyLifecycle);
            log.error("IOException during PR analysis: {}", e.getMessage(), e);
            Map<String, Object> errorEvent = Map.of(
                    "type", "error",
                    "message", "Analysis failed due to I/O error: " + e.getMessage());
            consumer.accept(executionEventBinding == null
                    ? errorEvent
                    : executionEventBinding.bindOwned(errorEvent));

            // Publish failed event
            publishAnalysisCompletedEvent(project, request, correlationId, startTime,
                    AnalysisCompletedEvent.CompletionStatus.FAILED, 0, 0,
                    e.getMessage(), executionEventBinding);

            Map<String, Object> result = Map.of(
                    "status", "error", "message", e.getMessage());
            return executionEventBinding == null
                    ? result
                    : executionEventBinding.bindOwned(result);
        } catch (GeneralSecurityException | RuntimeException error) {
            failPendingCoverage(executionManifest, "analysis_pipeline_failed");
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
        ExecutionPolicyConfig policyConfig = java.util.Objects.requireNonNull(
                executionPolicyRuntime.currentConfig(),
                "execution policy config is required");
        return freezePolicyPlan(
                project,
                request,
                policyConfig,
                stableRolloutKey(project, request));
    }

    private FrozenExecutionPlan freezePolicyPlan(
            Project project,
            PrProcessRequest request,
            ExecutionPolicyConfig policyConfig,
            StableRolloutKey stableRolloutKey) {
        if (executionPolicyRuntime == null) {
            return null;
        }
        java.util.Objects.requireNonNull(policyConfig, "policyConfig");
        java.util.Objects.requireNonNull(stableRolloutKey, "stableRolloutKey");
        String executionId = requestPolicyExecutionIdentity(
                project, request, policyConfig);
        return executionPolicyRuntime.freeze(
                executionId, stableRolloutKey, policyConfig);
    }

    private static StableRolloutKey stableRolloutKey(
            Project project,
            PrProcessRequest request) {
        Long projectId = project.getId();
        if (projectId == null || projectId <= 0 || request.getPullRequestId() == null) {
            throw new IllegalArgumentException("policy selection requires persisted project and pull request IDs");
        }
        if (project.getWorkspace() == null || project.getWorkspace().getId() == null
                || project.getWorkspace().getId() <= 0) {
            throw new IllegalArgumentException("policy selection requires a persisted workspace ID");
        }
        return StableRolloutKey.forProject(project.getWorkspace().getId(), projectId);
    }

    private static String requestPolicyExecutionIdentity(
            Project project,
            PrProcessRequest request,
            ExecutionPolicyConfig policyConfig) {
        java.util.Objects.requireNonNull(project, "project");
        java.util.Objects.requireNonNull(request, "request");
        java.util.Objects.requireNonNull(policyConfig, "policyConfig");

        ProjectAiConnectionBinding aiBinding = project.getAiBinding();
        AIConnection aiConnection = aiBinding == null
                ? null
                : aiBinding.getAiConnection();
        ProjectConfig projectConfig = project.getEffectiveConfig();
        AnalysisLimitsConfig limits = projectConfig == null
                ? null
                : projectConfig.analysisLimits();

        String identityInput = String.join("\n",
                "candidate-execution-input-v2",
                semanticIdentityPart(project.getId()),
                semanticIdentityPart(request.getPullRequestId()),
                semanticIdentityPart(request.getCommitHash()),
                semanticIdentityPart(request.getSourceBranchName()),
                semanticIdentityPart(request.getTargetBranchName()),
                semanticIdentityPart(request.getAnalysisType()),
                semanticIdentityPart(policyConfig.configRevision()),
                semanticIdentityPart(policyConfig.mode()),
                semanticIdentityPart(policyConfig.candidatePolicyVersion()),
                semanticIdentityPart(policyConfig.rolloutBasisPoints()),
                semanticIdentityPart(policyConfig.rolloutSalt()),
                CANDIDATE_PROMPT_CONTRACT_VERSION,
                semanticIdentityPart(aiConnection == null
                        ? null
                        : aiConnection.getProviderKey()),
                semanticIdentityPart(aiConnection == null
                        ? null
                        : aiConnection.getAiModel()),
                semanticIdentityPart(aiConnection == null
                        ? null
                        : aiConnection.getBaseUrl()),
                semanticIdentityPart(aiConnection == null
                        ? null
                        : aiConnection.getCustomParameters()),
                semanticIdentityPart(aiBinding == null
                        ? null
                        : aiBinding.getPolicyJson()),
                semanticIdentityPart(projectConfig == null
                        ? null
                        : projectConfig.maxAnalysisTokenLimit()),
                semanticIdentityPart(projectConfig == null
                        ? null
                        : projectConfig.useLocalMcp()),
                semanticIdentityPart(projectConfig == null
                        ? null
                        : projectConfig.useMcpTools()),
                semanticIdentityPart(projectConfig == null
                        ? null
                        : projectConfig.taskContextAnalysisEnabled()),
                semanticIdentityPart(projectConfig == null
                        ? null
                        : projectConfig.getProjectRulesConfig().toEnabledRulesJson()),
                semanticIdentityPart(projectConfig == null
                        ? null
                        : projectConfig.analysisScope()),
                semanticIdentityPart(limits == null ? null : limits.maxFiles()),
                semanticIdentityPart(limits == null ? null : limits.maxFileSizeBytes()),
                semanticIdentityPart(limits == null ? null : limits.maxTotalDiffSizeBytes()),
                semanticIdentityPart(limits == null ? null : limits.maxTotalTokens()));
        return "pr:" + sha256(identityInput);
    }

    static String candidateExecutionIdentity(
            Project project,
            PrProcessRequest request,
            ExecutionPolicyConfig policyConfig,
            AiAnalysisRequest acquiredRequest,
            String indexIdentity) {
        java.util.Objects.requireNonNull(acquiredRequest, "acquiredRequest");
        requireManifestEqual(
                acquiredRequest.getProjectId(), project.getId(), "projectId");
        requireManifestEqual(
                acquiredRequest.getProjectId(), request.getProjectId(), "projectId");
        requireManifestEqual(
                acquiredRequest.getPullRequestId(),
                request.getPullRequestId(),
                "pullRequestId");
        requireManifestEqual(
                acquiredRequest.getHeadSha(), request.getCommitHash(), "headSha");
        String provider = requiredManifestPart(
                acquiredRequest.getVcsProvider(), "vcsProvider")
                .toLowerCase(Locale.ROOT);
        String workspace = requiredManifestPart(
                acquiredRequest.getProjectVcsWorkspace(),
                "projectVcsWorkspace");
        String repository = requiredManifestPart(
                acquiredRequest.getProjectVcsRepoSlug(),
                "projectVcsRepoSlug");
        String baseSha = requiredManifestPart(
                acquiredRequest.getBaseSha(), "baseSha");
        String headSha = requiredManifestPart(
                acquiredRequest.getHeadSha(), "headSha");
        String mergeBaseSha = requiredManifestPart(
                acquiredRequest.getMergeBaseSha(), "mergeBaseSha");
        String rawDiff = java.util.Objects.requireNonNull(
                acquiredRequest.getRawDiff(),
                "rawDiff is required for candidate identity");
        if (acquiredRequest.getReconciliationFileContents() != null
                && !acquiredRequest.getReconciliationFileContents().isEmpty()) {
            throw new IllegalArgumentException(
                    "candidate reconciliation contents are not manifest-bound inputs");
        }
        PrEnrichmentDataDto enrichment = acquiredRequest instanceof AiAnalysisRequestImpl impl
                ? impl.getEnrichmentData()
                : null;
        String inputDigest = ExecutionInputArtifactBundle.canonicalInputDigest(
                rawDiff.getBytes(StandardCharsets.UTF_8), enrichment);
        String identityInput = String.join("\n",
                "candidate-execution-input-v3",
                semanticIdentityPart(requestPolicyExecutionIdentity(
                        project, request, policyConfig)),
                semanticIdentityPart(provider),
                semanticIdentityPart(workspace),
                semanticIdentityPart(repository),
                semanticIdentityPart(baseSha),
                semanticIdentityPart(headSha),
                semanticIdentityPart(mergeBaseSha),
                semanticIdentityPart(inputDigest),
                semanticIdentityPart(
                        ImmutableExecutionManifest.CURRENT_SCHEMA_VERSION),
                semanticIdentityPart(
                        ImmutableExecutionManifest.CURRENT_ARTIFACT_SCHEMA_VERSION),
                semanticIdentityPart(CANDIDATE_ARTIFACT_PRODUCER),
                semanticIdentityPart(CANDIDATE_ARTIFACT_PRODUCER_VERSION),
                semanticIdentityPart(requiredManifestPart(
                        indexIdentity, "indexIdentity")));
        return "pr:" + sha256(identityInput);
    }

    private static String semanticIdentityPart(Object value) {
        if (value == null) {
            return "-1:";
        }
        String text = String.valueOf(value);
        return text.length() + ":" + text;
    }

    private List<AiAnalysisRequest> acquireAiRequests(
            VcsAiClientService aiClientService,
            Project project,
            PrProcessRequest request,
            Optional<CodeAnalysis> previousAnalysis,
            List<CodeAnalysis> allPrAnalyses,
            boolean exactSnapshot,
            EventConsumer consumer,
            Instant acquisitionStartedAt) throws GeneralSecurityException {
        return acquireAiRequests(
                aiClientService,
                project,
                request,
                previousAnalysis,
                allPrAnalyses,
                exactSnapshot,
                consumer,
                acquisitionStartedAt,
                ignored -> { });
    }

    private List<AiAnalysisRequest> acquireAiRequests(
            VcsAiClientService aiClientService,
            Project project,
            PrProcessRequest request,
            Optional<CodeAnalysis> previousAnalysis,
            List<CodeAnalysis> allPrAnalyses,
            boolean exactSnapshot,
            EventConsumer consumer,
            Instant acquisitionStartedAt,
            ExactHeadAdmission exactHeadAdmission) throws GeneralSecurityException {
        try {
            return exactSnapshot
                    ? aiClientService.buildExactAiAnalysisRequests(
                            project,
                            request,
                            previousAnalysis,
                            allPrAnalyses,
                            exactHeadAdmission)
                    : aiClientService.buildAiAnalysisRequests(
                            project, request, previousAnalysis, allPrAnalyses);
        } catch (GeneralSecurityException | RuntimeException error) {
            // Exact acquisition has not produced a durable manifest yet, so a
            // candidate stage event cannot be truthfully identity-bound.
            if (!exactSnapshot) {
                emitStageTelemetry(
                        consumer,
                        "acquisition",
                        "java_vcs_diff",
                        "failed",
                        acquisitionStartedAt,
                        0,
                        "diff_acquisition_failed");
            }
            throw error;
        }
    }

    private ImmutableExecutionManifest persistCandidateManifest(
            AiAnalysisRequest aiRequest,
            PrProcessRequest processRequest,
            FrozenExecutionPlan policyPlan) {
        if (executionManifestService == null) {
            throw new IllegalStateException(
                    "candidate execution requires durable manifest persistence");
        }
        if (policyPlan == null || !isCandidatePath(policyPlan)) {
            throw new IllegalArgumentException(
                    "candidate manifest requires a frozen candidate policy plan");
        }

        Long projectId = aiRequest.getProjectId();
        Long pullRequestId = aiRequest.getPullRequestId();
        if (projectId == null || pullRequestId == null) {
            throw new IllegalArgumentException(
                    "candidate manifest requires persisted project and pull-request IDs");
        }
        requireManifestEqual(projectId, processRequest.getProjectId(), "projectId");
        requireManifestEqual(
                pullRequestId, processRequest.getPullRequestId(), "pullRequestId");
        requireManifestEqual(
                aiRequest.getHeadSha(), processRequest.getCommitHash(), "headSha");
        String provider = requiredManifestPart(aiRequest.getVcsProvider(), "vcsProvider")
                .toLowerCase(Locale.ROOT);
        String workspace = requiredManifestPart(
                aiRequest.getProjectVcsWorkspace(), "projectVcsWorkspace");
        String repository = requiredManifestPart(
                aiRequest.getProjectVcsRepoSlug(), "projectVcsRepoSlug");
        String rawDiff = java.util.Objects.requireNonNull(
                aiRequest.getRawDiff(), "rawDiff is required for candidate manifest");
        byte[] rawDiffBytes = rawDiff.getBytes(StandardCharsets.UTF_8);
        String diffDigest = sha256(rawDiff);
        String diffArtifactId = "diff:" + sha256(
                policyPlan.primary().executionId() + '\0' + diffDigest);
        PrEnrichmentDataDto enrichment = aiRequest instanceof AiAnalysisRequestImpl impl
                ? impl.getEnrichmentData()
                : null;
        if (aiRequest.getReconciliationFileContents() != null
                && !aiRequest.getReconciliationFileContents().isEmpty()) {
            throw new IllegalArgumentException(
                    "candidate reconciliation contents are not manifest-bound inputs");
        }
        ExecutionInputArtifactBundle inputBundle = ExecutionInputArtifactBundle.create(
                policyPlan.primary().executionId(),
                aiRequest.getHeadSha(),
                diffArtifactId,
                rawDiffBytes,
                enrichment,
                ImmutableExecutionManifest.CURRENT_ARTIFACT_SCHEMA_VERSION,
                CANDIDATE_ARTIFACT_PRODUCER,
                CANDIDATE_ARTIFACT_PRODUCER_VERSION);

        ImmutableExecutionManifest proposed = ImmutableExecutionManifest.create(
                ImmutableExecutionManifest.CURRENT_SCHEMA_VERSION,
                policyPlan.primary().executionId(),
                projectId,
                provider + ":" + workspace + "/" + repository,
                pullRequestId,
                aiRequest.getBaseSha(),
                aiRequest.getHeadSha(),
                aiRequest.getMergeBaseSha(),
                diffArtifactId,
                diffDigest,
                rawDiffBytes.length,
                ImmutableExecutionManifest.RAW_DIFF_ARTIFACT_KIND,
                CANDIDATE_ARTIFACT_PRODUCER,
                CANDIDATE_ARTIFACT_PRODUCER_VERSION,
                ImmutableExecutionManifest.CURRENT_ARTIFACT_SCHEMA_VERSION,
                policyPlan.primary().policyVersion(),
                creationFence(policyPlan),
                policyPlan.primary().createdAt(),
                inputBundle.entries());
        return executionManifestService.persistBeforeWork(
                proposed, inputBundle.artifacts());
    }

    private ImmutableExecutionManifest requireReloadedCandidateManifest(
            ImmutableExecutionManifest persisted) {
        if (persisted == null) {
            throw new IllegalStateException("candidate manifest persistence returned no state");
        }
        ImmutableExecutionManifest reloaded = executionManifestService.requireVerified(
                persisted.executionId());
        if (!persisted.equals(reloaded)) {
            throw new IllegalStateException(
                    "reloaded candidate manifest conflicts with persisted state");
        }
        return reloaded;
    }

    private static String requiredManifestPart(String value, String field) {
        if (value == null || value.isBlank()) {
            throw new IllegalArgumentException(field + " is required for candidate manifest");
        }
        return value;
    }

    private static void requireManifestEqual(
            Object observed,
            Object expected,
            String field) {
        if (!java.util.Objects.equals(observed, expected)) {
            throw new IllegalArgumentException(
                    field + " conflicts with accepted candidate request");
        }
    }

    static String creationFence(FrozenExecutionPlan policyPlan) {
        String coordinates = policyPlan.primary().executionId()
                + '\0' + policyPlan.stableRolloutKeyHash()
                + '\0' + policyPlan.primary().createdAt();
        return "creation:" + sha256(coordinates);
    }

    private static void requireCandidateOutputBinding(
            CodeAnalysis analysis,
            ImmutableExecutionManifest manifest) {
        Long persistedProjectId = analysis == null || analysis.getProject() == null
                ? null
                : analysis.getProject().getId();
        if (analysis == null
                || !analysis.hasExecutionIdentity()
                || !java.util.Objects.equals(
                        analysis.getExecutionId(), manifest.executionId())
                || !java.util.Objects.equals(
                        analysis.getArtifactManifestDigest(),
                        manifest.artifactManifestDigest())
                || !java.util.Objects.equals(
                        persistedProjectId, manifest.projectId())
                || !java.util.Objects.equals(
                        analysis.getPrNumber(), manifest.pullRequestId())
                || !java.util.Objects.equals(
                        analysis.getCommitHash(), manifest.headSha())) {
            throw new IllegalStateException(
                    "persisted candidate output conflicts with immutable execution manifest");
        }
    }

    private Map<String, Object> noAnalyzableChanges(
            Project project,
            PrProcessRequest request,
            EventConsumer consumer,
            String correlationId,
            Instant startTime,
            Instant acquisitionStartedAt,
            ExecutionLifecycle policyLifecycle,
            ExecutionEventBinding eventBinding) {
        String message = "No changed files match the project analysis scope";
        emitStageTelemetry(
                consumer,
                "acquisition",
                "java_vcs_diff",
                "skipped",
                acquisitionStartedAt,
                0,
                "no_analyzable_changes",
                eventBinding);
        log.info("Skipping PR analysis for project={}, PR={}: {}",
                project.getId(), request.getPullRequestId(), message);
        Map<String, Object> info = Map.of("type", "info", "message", message);
        consumer.accept(eventBinding == null ? info : eventBinding.bindOwned(info));
        publishAnalysisCompletedEvent(
                project,
                request,
                correlationId,
                startTime,
                AnalysisCompletedEvent.CompletionStatus.SUCCESS,
                0,
                0,
                null,
                eventBinding);
        completePolicyLifecycle(policyLifecycle);
        Map<String, Object> result = Map.of("status", "ignored", "message", message);
        return eventBinding == null ? result : eventBinding.bindOwned(result);
    }

    private Map<String, Object> noCoverageAnchors(
            Project project,
            PrProcessRequest request,
            EventConsumer consumer,
            String correlationId,
            Instant startTime,
            Instant acquisitionStartedAt,
            ExecutionLifecycle policyLifecycle,
            ExecutionEventBinding eventBinding,
            CoverageLedgerSnapshot coverage) {
        String message = "No mandatory review anchors match the project analysis scope";
        emitStageTelemetry(
                consumer,
                "acquisition",
                "java_coverage_ledger",
                "complete",
                acquisitionStartedAt,
                coverage.counts().inventory(),
                "authoritative_empty",
                eventBinding);
        Map<String, Object> info = Map.of(
                "type", "info",
                "message", message,
                "analysisState", CoverageAnalysisState.EMPTY.name(),
                "coverageCounts", coverageCountsPayload(coverage.counts()));
        consumer.accept(eventBinding == null ? info : eventBinding.bindOwned(info));
        publishAnalysisCompletedEvent(
                project,
                request,
                correlationId,
                startTime,
                AnalysisCompletedEvent.CompletionStatus.SUCCESS,
                0,
                0,
                null,
                eventBinding);
        completePolicyLifecycle(policyLifecycle);
        Map<String, Object> result = new LinkedHashMap<>();
        result.put("status", "empty");
        result.put("message", message);
        result.put("analysisState", CoverageAnalysisState.EMPTY.name());
        result.put("coverageCounts", coverageCountsPayload(coverage.counts()));
        return eventBinding == null ? result : eventBinding.bindOwned(result);
    }

    private static Set<String> eligibleCoveragePaths(AiAnalysisRequest request) {
        Set<String> paths = new LinkedHashSet<>();
        if (request.getChangedFiles() != null) {
            paths.addAll(request.getChangedFiles());
        }
        if (request.getDeletedFiles() != null) {
            paths.addAll(request.getDeletedFiles());
        }
        paths.removeIf(path -> path == null || path.isBlank());
        return Collections.unmodifiableSet(paths);
    }

    private static CoverageReceipt coverageReceipt(Map<String, Object> aiResponse)
            throws IOException {
        if (aiResponse == null
                || !(aiResponse.get("coverageReceipt") instanceof Map<?, ?> receipt)) {
            throw new IOException("Candidate v2 response is missing coverageReceipt");
        }
        Object rawDispositions = receipt.get("dispositions");
        if (!(rawDispositions instanceof List<?> values)) {
            throw new IOException(
                    "Candidate v2 coverageReceipt dispositions are missing");
        }
        List<CoverageDisposition> dispositions = new java.util.ArrayList<>(values.size());
        for (Object value : values) {
            if (!(value instanceof Map<?, ?> disposition)) {
                throw new IOException(
                        "Candidate v2 coverageReceipt contains a malformed disposition");
            }
            String stateName = requiredReceiptString(disposition, "state");
            CoverageAnchorState state;
            try {
                state = CoverageAnchorState.valueOf(stateName);
            } catch (IllegalArgumentException error) {
                throw new IOException(
                        "Candidate v2 coverageReceipt contains an unknown anchor state",
                        error);
            }
            Object rawReason = disposition.get("reasonCode");
            if (rawReason != null && !(rawReason instanceof String)) {
                throw new IOException(
                        "Candidate v2 coverageReceipt reasonCode is malformed");
            }
            dispositions.add(new CoverageDisposition(
                    requiredReceiptString(disposition, "anchorId"),
                    state,
                    (String) rawReason));
        }
        return new CoverageReceipt(
                requiredReceiptNumber(receipt, "schemaVersion").intValue(),
                requiredReceiptString(receipt, "executionId"),
                requiredReceiptString(receipt, "artifactManifestDigest"),
                requiredReceiptString(receipt, "diffDigest"),
                requiredReceiptNumber(receipt, "diffByteLength").longValue(),
                requiredReceiptString(receipt, "ledgerDigest"),
                dispositions);
    }

    private static String requiredReceiptString(Map<?, ?> receipt, String field)
            throws IOException {
        Object value = receipt.get(field);
        if (!(value instanceof String stringValue) || stringValue.isBlank()) {
            throw new IOException(
                    "Candidate v2 coverageReceipt " + field + " is missing or malformed");
        }
        return stringValue;
    }

    private static Number requiredReceiptNumber(Map<?, ?> receipt, String field)
            throws IOException {
        Object value = receipt.get(field);
        if (!(value instanceof Number number)) {
            throw new IOException(
                    "Candidate v2 coverageReceipt " + field + " is missing or malformed");
        }
        return number;
    }

    private static Map<String, Object> attachCoverageTruth(
            Map<String, Object> aiResponse,
            CoverageLedgerSnapshot coverage) {
        Map<String, Object> result = new LinkedHashMap<>(aiResponse);
        result.put("analysisState", coverage.analysisState().name());
        result.put("coverageCounts", coverageCountsPayload(coverage.counts()));
        if (coverage.analysisState() == CoverageAnalysisState.PARTIAL) {
            CoverageCounts counts = coverage.counts();
            String warning = "> **Partial review coverage:** "
                    + counts.examined() + " examined, "
                    + counts.unsupported() + " unsupported, "
                    + counts.failed() + " failed, and "
                    + counts.incomplete() + " incomplete out of "
                    + counts.inventory() + " exact anchors.\n\n";
            Object existing = result.get("comment");
            result.put("comment", warning + (existing == null ? "" : existing));
        }
        return result;
    }

    private static Map<String, Object> coverageCountsPayload(CoverageCounts counts) {
        Map<String, Object> result = new LinkedHashMap<>();
        result.put("inventory", counts.inventory());
        result.put("pending", counts.pending());
        result.put("ownerPending", counts.ownerPending());
        result.put("examined", counts.examined());
        result.put("incomplete", counts.incomplete());
        result.put("unsupported", counts.unsupported());
        result.put("failed", counts.failed());
        result.put("policyExcluded", counts.policyExcluded());
        result.put("deletedRecorded", counts.deletedRecorded());
        return Collections.unmodifiableMap(result);
    }

    private LatestHeadRegistration registerLatestHead(
            FrozenExecutionPlan policyPlan,
            PublicationKey publicationKey,
            long generation) {
        if (policyPlan == null || publicationKey == null) {
            throw new IllegalArgumentException(
                    "candidate latest-head registration requires frozen coordinates");
        }
        if (generation <= 0L) {
            throw new IllegalArgumentException(
                    "candidate latest-head generation must be positive");
        }
        return requirePublicationFence().registerLatestHead(
                policyPlan.primary(), publicationKey, generation);
    }

    private PublicationFence requirePublicationFence() {
        if (executionPolicyRuntime == null) {
            throw new IllegalStateException(
                    "candidate latest-head fencing requires an execution policy runtime");
        }
        return java.util.Objects.requireNonNull(
                executionPolicyRuntime.publicationFence(),
                "candidate latest-head fencing requires a publication fence");
    }

    private boolean isLatestHead(
            FrozenExecutionPlan policyPlan,
            PublicationKey publicationKey) {
        if (executionPolicyRuntime == null
                || policyPlan == null
                || publicationKey == null) {
            return true;
        }
        return requirePublicationFence().isLatestHead(
                policyPlan.primary(), publicationKey);
    }

    private static Map<String, Object> supersededResult(
            EventConsumer consumer,
            ExecutionEventBinding eventBinding) {
        return supersededResult(consumer, eventBinding, null);
    }

    private static Map<String, Object> supersededResult(
            EventConsumer consumer,
            ExecutionEventBinding eventBinding,
            Object computeState) {
        Map<String, Object> notice = new LinkedHashMap<>();
        notice.put("type", "info");
        notice.put("status", "superseded");
        notice.put("reason", "latest_head_advanced");
        if (computeState != null) {
            notice.put("computeState", computeState);
        }
        consumer.accept(eventBinding == null
                ? notice
                : eventBinding.bindOwned(notice));
        Map<String, Object> result = new LinkedHashMap<>();
        result.put("status", "superseded");
        result.put("reason", "latest_head_advanced");
        if (computeState != null) {
            result.put("computeState", computeState);
        }
        return eventBinding == null ? result : eventBinding.bindOwned(result);
    }

    private void failPendingCoverage(
            ImmutableExecutionManifest executionManifest,
            String reasonCode) {
        if (coverageLedgerService == null || executionManifest == null) {
            return;
        }
        try {
            CoverageLedgerSnapshot current = coverageLedgerService.requireSnapshot(
                    executionManifest.executionId());
            if (current.analysisState() == CoverageAnalysisState.PENDING) {
                coverageLedgerService.failOpenAnchors(executionManifest, reasonCode);
            }
        } catch (RuntimeException coverageError) {
            log.warn(
                    "Failed to terminalize pending coverage ledger for execution {}: {}",
                    executionManifest.executionId(),
                    coverageError.getClass().getSimpleName());
        }
    }

    private void supersedeCoverage(
            ImmutableExecutionManifest executionManifest) {
        if (coverageLedgerService == null || executionManifest == null) {
            return;
        }
        try {
            coverageLedgerService.supersede(
                    executionManifest.executionId(), "analysis_superseded");
        } catch (RuntimeException coverageError) {
            log.warn(
                    "Failed to supersede coverage ledger for execution {}: {}",
                    executionManifest.executionId(),
                    coverageError.getClass().getSimpleName());
        }
    }

    private static boolean isCandidatePath(FrozenExecutionPlan policyPlan) {
        return policyPlan != null && policyPlan.primary().candidatePath();
    }

    private Map<String, Object> performAiAnalysis(
            AiAnalysisRequest aiRequest,
            Consumer<Map<String, Object>> aiEventConsumer,
            FrozenExecutionPlan policyPlan,
            String indexVersion,
            ImmutableExecutionManifest executionManifest,
            CoverageWorkPlan coverageWorkPlan)
            throws IOException, GeneralSecurityException {
        boolean exactIndex = indexVersion != null
                && EXACT_INDEX_VERSION.matcher(indexVersion).matches();
        if (executionManifest != null) {
            if (!isCandidatePath(policyPlan)) {
                throw new IllegalStateException(
                        "immutable manifest cannot be sent without a candidate policy path");
            }
            if (!CANDIDATE_INDEX_IDENTITY.equals(indexVersion)) {
                throw new IllegalStateException(
                        "manifest-bound candidate execution requires RAG retrieval to be disabled");
            }
            if (coverageWorkPlan == null) {
                throw new IllegalStateException(
                        "candidate execution requires a coverage work plan");
            }
            return aiAnalysisClient.performAnalysis(
                    aiRequest,
                    aiEventConsumer,
                    policyPlan.primary(),
                    indexVersion,
                    executionManifest,
                    coverageWorkPlan);
        }
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

    private static String sha256(String value) {
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
            EventConsumer consumer,
            ExecutionEventBinding eventBinding) {
        emitStageTelemetry(
                consumer,
                "policy",
                "java_execution_policy",
                "cancelled",
                startTime,
                0,
                "kill_switch",
                eventBinding);
        publishAnalysisCompletedEvent(
                project,
                request,
                correlationId,
                startTime,
                AnalysisCompletedEvent.CompletionStatus.CANCELLED,
                0,
                0,
                "Policy kill switch",
                eventBinding);
        Map<String, Object> result = Map.of(
                "status", "cancelled", "reason", "policy_kill_switch");
        return eventBinding == null ? result : eventBinding.bindOwned(result);
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

    private void emitPolicySelection(
            EventConsumer consumer,
            FrozenExecutionPlan plan,
            ExecutionEventBinding eventBinding) {
        if (plan == null) {
            return;
        }
        try {
            Map<String, Object> event = new LinkedHashMap<>();
            event.put("type", "policy_selection");
            event.put("schemaVersion", 1);
            event.put("policyVersion", plan.primary().policyVersion());
            event.put("mode", plan.primary().mode().name().toLowerCase(java.util.Locale.ROOT));
            event.put("reason", plan.primary().selectionReason().name().toLowerCase(java.util.Locale.ROOT));
            event.put("configRevision", plan.configRevision());
            consumer.accept(eventBinding == null
                    ? Collections.unmodifiableMap(event)
                    : eventBinding.bindOwned(event));
        } catch (RuntimeException error) {
            if (eventBinding != null) {
                throw error;
            }
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
            String headRevision,
            ExecutionEventBinding eventBinding) throws IOException {
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
                String reason = switch (reservation) {
                    case SHADOW_DENIED -> "shadow_publication_blocked";
                    case STALE_HEAD -> "stale_publication_blocked";
                    case DUPLICATE -> "duplicate_publication_blocked";
                    case RESERVED -> throw new IllegalStateException(
                            "reserved publication entered denial branch");
                };
                emitStageTelemetry(
                        consumer,
                        "delivery",
                        "java_vcs_reporting",
                        "skipped",
                        deliveryStartedAt,
                        issues,
                        reason,
                        eventBinding);
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
                null,
                eventBinding);
        return true;
    }

    private boolean deliverCandidateAnalysis(
            ReviewDeliveryService deliveryService,
            EventConsumer consumer,
            Instant deliveryStartedAt,
            int issues,
            CodeAnalysis analysis,
            Project project,
            Long pullRequestId,
            String headRevision,
            String repositoryId,
            long headGeneration,
            ExecutionEventBinding eventBinding) throws IOException {
        if (!analysis.hasExecutionIdentity() || analysis.getId() == null) {
            throw new IllegalStateException(
                    "durable candidate delivery requires persisted analysis identity");
        }
        EVcsProvider provider = ProjectVcsInfoRetriever.getVcsProvider(project);
        String providerKey = provider.name().toLowerCase(Locale.ROOT);
        String truthDigest = ReviewDeliveryTruth.digest(analysis);
        String reportArtifactId = "review-output:" + truthDigest;
        String reportDigest = truthDigest;
        String publicationKind = "ANALYSIS_RESULTS";
        if (project.getWorkspace() == null
                || project.getWorkspace().getId() == null) {
            throw new IllegalStateException(
                    "durable delivery requires a tenant identity");
        }
        String idempotencyKey = ReviewProviderEffectIdentity.derive(
                project.getWorkspace().getId(),
                providerKey,
                repositoryId,
                pullRequestId,
                headRevision,
                reportDigest,
                publicationKind);
        String intentId = "delivery:" + ReviewDeliveryTruth.stableId(
                "review-delivery-intent-v1", idempotencyKey);
        ReviewDeliveryIntent intent = new ReviewDeliveryIntent(
                intentId,
                analysis.getExecutionId(),
                analysis.getArtifactManifestDigest(),
                headRevision.toLowerCase(Locale.ROOT),
                headGeneration,
                reportArtifactId,
                reportDigest,
                truthDigest,
                providerKey,
                project.getId(),
                pullRequestId,
                publicationKind,
                idempotencyKey);
        if (deliveryService.enqueue(intent).isEmpty()) {
            emitStageTelemetry(
                    consumer,
                    "delivery",
                    "java_vcs_reporting",
                    "skipped",
                    deliveryStartedAt,
                    issues,
                    "stale_head",
                    eventBinding);
            return false;
        }
        ReviewDeliveryOutcome outcome = deliveryService.attempt(
                intentId, Instant.now());
        if (outcome.state() == ReviewDeliveryState.DELIVERED) {
            emitStageTelemetry(
                    consumer,
                    "delivery",
                    "java_vcs_reporting",
                    "complete",
                    deliveryStartedAt,
                    issues,
                    null,
                    eventBinding);
            return true;
        }
        if (outcome.state() == ReviewDeliveryState.STALE) {
            emitStageTelemetry(
                    consumer,
                    "delivery",
                    "java_vcs_reporting",
                    "skipped",
                    deliveryStartedAt,
                    issues,
                    outcome.reasonCode(),
                    eventBinding);
            return false;
        }
        if (outcome.state() == ReviewDeliveryState.RETRYABLE_FAILED) {
            throw new IOException("durable VCS delivery is retryable: "
                    + outcome.reasonCode());
        }
        if (outcome.state() == ReviewDeliveryState.PERMANENT_FAILED
                || outcome.state() == ReviewDeliveryState.AMBIGUOUS) {
            throw new IOException("durable VCS delivery is terminal: "
                    + outcome.state() + ":" + outcome.reasonCode());
        }
        throw new IllegalStateException(
                "delivery attempt returned non-terminal state " + outcome.state());
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
        return finalizePipelineTelemetry(
                aiResponse, consumer, executionStartedAt, javaStages, null, null);
    }

    @SuppressWarnings("unchecked")
    private Map<String, Object> finalizePipelineTelemetry(
            Map<String, Object> aiResponse,
            EventConsumer consumer,
            Instant executionStartedAt,
            List<StageObservation> javaStages,
            ImmutableExecutionManifest executionManifest,
            String indexVersion) {
        ExecutionEventBinding eventBinding = executionManifest == null
                ? null
                : ExecutionEventBinding.fromManifest(executionManifest);
        if (aiResponse == null) {
            return null;
        }
        Object rawTelemetry = aiResponse.get("telemetry");
        if (!(rawTelemetry instanceof Map<?, ?> rawMap)) {
            emitTerminalUnavailable(
                    consumer, "python_snapshot_unavailable", eventBinding);
            return eventBinding == null
                    ? aiResponse
                    : eventBinding.bindOwned(aiResponse);
        }
        Map<String, Object> finalized;
        try {
            Map<String, Object> snapshot = new LinkedHashMap<>();
            rawMap.forEach((key, value) -> snapshot.put(String.valueOf(key), value));
            long durationMs = Math.max(
                    0L, Duration.between(executionStartedAt, Instant.now()).toMillis());
            finalized = executionManifest == null
                    ? PipelineTelemetryFinalizer.finalizeDocument(
                            snapshot, javaStages, durationMs)
                    : PipelineTelemetryFinalizer.finalizeDocument(
                            snapshot,
                            javaStages,
                            durationMs,
                            executionManifest,
                            indexVersion);
        } catch (RuntimeException error) {
            log.warn("Terminal telemetry finalization rejected: {}", error.getClass().getSimpleName());
            emitTerminalUnavailable(
                    consumer, "terminal_contract_rejected", eventBinding);
            return eventBinding == null
                    ? aiResponse
                    : eventBinding.bindOwned(aiResponse);
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
            consumer.accept(eventBinding == null
                    ? Collections.unmodifiableMap(event)
                    : eventBinding.bindOwned(event));
        } catch (RuntimeException error) {
            // The finalized analysis artifact remains valid even when its
            // observational event stream is unavailable.
            log.warn("Terminal telemetry event emission failed: {}", error.getClass().getSimpleName());
        }
        return eventBinding == null ? result : eventBinding.bindOwned(result);
    }

    private void emitTerminalUnavailable(
            EventConsumer consumer,
            String reason,
            ExecutionEventBinding eventBinding) {
        try {
            Map<String, Object> event = Map.of(
                    "type", "telemetry",
                    "state", "not_emitted",
                    "reason", reason);
            consumer.accept(eventBinding == null
                    ? event
                    : eventBinding.bindOwned(event));
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
        emitStageTelemetry(
                consumer, stage, producer, outcome, startedAt, itemCount, reasonCode, null);
    }

    private void emitStageTelemetry(EventConsumer consumer,
                                    String stage,
                                    String producer,
                                    String outcome,
                                    Instant startedAt,
                                    int itemCount,
                                    String reasonCode,
                                    ExecutionEventBinding eventBinding) {
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
            consumer.accept(eventBinding == null
                    ? Collections.unmodifiableMap(event)
                    : eventBinding.bindOwned(event));
        } catch (RuntimeException error) {
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
    private void publishAnalysisStartedEvent(
            Project project,
            PrProcessRequest request,
            String correlationId) {
        publishAnalysisStartedEvent(project, request, correlationId, null);
    }

    private void publishAnalysisStartedEvent(
            Project project,
            PrProcessRequest request,
            String correlationId,
            ExecutionEventBinding eventBinding) {
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
                    null, // jobId not available at this level
                    eventBinding == null ? null : eventBinding.executionId(),
                    eventBinding == null ? null : eventBinding.artifactManifestDigest()
            );
            eventPublisher.publishEvent(event);
            log.debug("Published AnalysisStartedEvent for PR analysis: project={}, pr={}",
                    project.getId(), request.getPullRequestId());
        } catch (Exception e) {
            if (eventBinding != null && e instanceof RuntimeException runtime) {
                throw runtime;
            }
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
        publishAnalysisCompletedEvent(
                project,
                request,
                correlationId,
                startTime,
                status,
                issuesFound,
                filesAnalyzed,
                errorMessage,
                null);
    }

    private void publishAnalysisCompletedEvent(Project project, PrProcessRequest request,
                                               String correlationId, Instant startTime,
                                               AnalysisCompletedEvent.CompletionStatus status, int issuesFound,
                                               int filesAnalyzed, String errorMessage,
                                               ExecutionEventBinding eventBinding) {
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
            if (eventBinding != null) {
                metrics.put("executionId", eventBinding.executionId());
                metrics.put("artifactManifestDigest", eventBinding.artifactManifestDigest());
            }
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
                    request.getPullRequestId(),
                    eventBinding == null ? null : eventBinding.executionId(),
                    eventBinding == null ? null : eventBinding.artifactManifestDigest());
            eventPublisher.publishEvent(event);
            log.debug("Published AnalysisCompletedEvent for PR analysis: project={}, pr={}, status={}, duration={}ms",
                    project.getId(), request.getPullRequestId(), status, duration.toMillis());
        } catch (Exception e) {
            if (eventBinding != null && e instanceof RuntimeException runtime) {
                throw runtime;
            }
            log.warn("Failed to publish AnalysisCompletedEvent: {}", e.getMessage());
        }
    }

    private record ExecutionEventBinding(
            String executionId,
            String artifactManifestDigest) {
        private static final Pattern SHA_256 = Pattern.compile("[0-9a-f]{64}");

        private ExecutionEventBinding {
            if (executionId == null || executionId.isBlank()) {
                throw new IllegalArgumentException("candidate event executionId is missing");
            }
            if (artifactManifestDigest == null
                    || !SHA_256.matcher(artifactManifestDigest).matches()) {
                throw new IllegalArgumentException(
                        "candidate event artifactManifestDigest is invalid");
            }
        }

        private static ExecutionEventBinding require(
                FrozenExecutionPlan policyPlan,
                ImmutableExecutionManifest manifest) {
            if (!isCandidatePath(policyPlan) || manifest == null) {
                throw new IllegalStateException(
                        "candidate event emission requires an immutable manifest");
            }
            if (!policyPlan.primary().executionId().equals(manifest.executionId())) {
                throw new IllegalStateException(
                        "candidate event executionId conflicts with immutable manifest");
            }
            return fromManifest(manifest);
        }

        private static ExecutionEventBinding fromManifest(
                ImmutableExecutionManifest manifest) {
            java.util.Objects.requireNonNull(manifest, "manifest");
            return new ExecutionEventBinding(
                    manifest.executionId(), manifest.artifactManifestDigest());
        }

        private Map<String, Object> bindOwned(Map<String, Object> source) {
            java.util.Objects.requireNonNull(source, "candidate event");
            requireCompatible(source.get("executionId"), executionId, "executionId");
            requireCompatible(
                    source.get("artifactManifestDigest"),
                    artifactManifestDigest,
                    "artifactManifestDigest");
            Map<String, Object> bound = new LinkedHashMap<>(source);
            bound.put("executionId", executionId);
            bound.put("artifactManifestDigest", artifactManifestDigest);
            return Collections.unmodifiableMap(bound);
        }

        private Map<String, Object> requireProducerBound(
                Map<String, Object> source) {
            java.util.Objects.requireNonNull(source, "candidate producer event");
            requireExact(source.get("executionId"), executionId, "executionId");
            requireExact(
                    source.get("artifactManifestDigest"),
                    artifactManifestDigest,
                    "artifactManifestDigest");
            return Collections.unmodifiableMap(new LinkedHashMap<>(source));
        }

        private static void requireCompatible(
                Object observed,
                String expected,
                String field) {
            if (observed != null && !expected.equals(observed)) {
                throw new IllegalStateException(
                        "candidate event " + field + " conflicts with immutable manifest");
            }
        }

        private static void requireExact(
                Object observed,
                String expected,
                String field) {
            if (!(observed instanceof String value)) {
                throw new IllegalStateException(
                        "candidate producer event " + field
                                + " is missing or malformed");
            }
            if (!expected.equals(value)) {
                throw new IllegalStateException(
                        "candidate producer event " + field
                                + " conflicts with immutable manifest");
            }
        }
    }
}
