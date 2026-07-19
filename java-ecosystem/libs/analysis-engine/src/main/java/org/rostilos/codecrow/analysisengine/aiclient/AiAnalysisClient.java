package org.rostilos.codecrow.analysisengine.aiclient;

import com.fasterxml.jackson.core.type.TypeReference;
import com.fasterxml.jackson.databind.ObjectMapper;
import org.rostilos.codecrow.analysisengine.dto.request.ai.AiAnalysisRequest;
import org.rostilos.codecrow.analysisengine.dto.request.ai.AiAnalysisRequestImpl;
import org.rostilos.codecrow.analysisengine.dto.request.ai.AgenticRepositoryArchiveV1;
import org.rostilos.codecrow.analysisengine.dto.request.ai.enrichment.PrEnrichmentDataDto;
import org.rostilos.codecrow.analysisengine.coverage.CoverageAnalysisState;
import org.rostilos.codecrow.analysisengine.coverage.CoverageAnchor;
import org.rostilos.codecrow.analysisengine.coverage.CoverageAnchorState;
import org.rostilos.codecrow.analysisengine.coverage.CoverageCounts;
import org.rostilos.codecrow.analysisengine.coverage.CoverageDisposition;
import org.rostilos.codecrow.analysisengine.coverage.CoverageWorkPlan;
import org.rostilos.codecrow.analysisengine.execution.ImmutableExecutionManifest;
import org.rostilos.codecrow.analysisengine.execution.ExecutionInputArtifactBundle;
import org.rostilos.codecrow.analysisengine.execution.RagExecutionConfigV1;
import org.rostilos.codecrow.analysisengine.policy.PolicyExecution;
import org.rostilos.codecrow.core.model.ai.LlmModel;
import org.rostilos.codecrow.core.persistence.repository.ai.LlmModelRepository;
import org.slf4j.Logger;
import org.slf4j.LoggerFactory;
import org.springframework.beans.factory.annotation.Autowired;
import org.springframework.beans.factory.annotation.Qualifier;
import org.rostilos.codecrow.queue.RedisQueueService;
import org.springframework.stereotype.Service;
import org.springframework.web.client.RestTemplate;

import java.io.IOException;
import java.math.BigInteger;
import java.nio.charset.StandardCharsets;
import java.security.GeneralSecurityException;
import java.util.LinkedHashMap;
import java.util.HashSet;
import java.util.List;
import java.util.Map;
import java.util.Set;
import java.util.UUID;
import java.util.Locale;
import java.util.Objects;
import java.util.concurrent.TimeUnit;
import java.util.function.LongSupplier;

/**
 * Client for communicating with the AI analysis service (Inference
 * Orchestrator).
 * Uses an async queue architecture backed by Redis via codecrow-queue.
 * Always sends the FULL diff as a single request — token-safe file batching
 * is handled by the Python multi-stage pipeline's Stage 1.
 */
@Service
public class AiAnalysisClient {
    private static final Logger log = LoggerFactory.getLogger(AiAnalysisClient.class);

    private final RedisQueueService queueService;
    private final ObjectMapper objectMapper;
    private final LongSupplier currentTimeMillis;
    private final LlmModelRepository llmModelRepository;

    private static final int REVIEW_TIMEOUT_MINUTES = 30;
    private static final String JOBS_QUEUE_KEY = "codecrow:analysis:jobs";
    private static final String LEGACY_COMPATIBILITY_DEADLINE =
            "2026-09-30T00:00:00Z";

    public AiAnalysisClient(
            @Qualifier("aiRestTemplate") RestTemplate restTemplate,
            RedisQueueService queueService,
            ObjectMapper objectMapper) {
        this(restTemplate, queueService, objectMapper, null,
                System::currentTimeMillis);
    }

    @Autowired
    public AiAnalysisClient(
            @Qualifier("aiRestTemplate") RestTemplate restTemplate,
            RedisQueueService queueService,
            ObjectMapper objectMapper,
            LlmModelRepository llmModelRepository) {
        this(restTemplate, queueService, objectMapper, llmModelRepository,
                System::currentTimeMillis);
    }

    AiAnalysisClient(
            RestTemplate restTemplate,
            RedisQueueService queueService,
            ObjectMapper objectMapper,
            LongSupplier currentTimeMillis) {
        this(restTemplate, queueService, objectMapper, null, currentTimeMillis);
    }

    AiAnalysisClient(
            RestTemplate restTemplate,
            RedisQueueService queueService,
            ObjectMapper objectMapper,
            LlmModelRepository llmModelRepository,
            LongSupplier currentTimeMillis) {
        // restTemplate kept in constructor for backward compatibility but no longer
        // used
        this.queueService = queueService;
        this.objectMapper = objectMapper;
        this.llmModelRepository = llmModelRepository;
        this.currentTimeMillis = currentTimeMillis;
    }

    public Map<String, Object> performAnalysis(AiAnalysisRequest request)
            throws IOException, GeneralSecurityException {
        return performAnalysis(request, null);
    }

    public Map<String, Object> performAnalysis(AiAnalysisRequest request,
            java.util.function.Consumer<Map<String, Object>> eventHandler)
            throws IOException, GeneralSecurityException {
        return performAnalysis(request, eventHandler, null);
    }

    public Map<String, Object> performAnalysis(
            AiAnalysisRequest request,
            java.util.function.Consumer<Map<String, Object>> eventHandler,
            PolicyExecution policyExecution)
            throws IOException, GeneralSecurityException {
        return performAnalysis(request, eventHandler, policyExecution, null);
    }

    public Map<String, Object> performAnalysis(
            AiAnalysisRequest request,
            java.util.function.Consumer<Map<String, Object>> eventHandler,
            PolicyExecution policyExecution,
            String indexVersion)
            throws IOException, GeneralSecurityException {
        return performAnalysisInternal(
                request, eventHandler, policyExecution, indexVersion, null, null, null);
    }

    /**
     * Sends the candidate v2 queue shape with the durable exact-hunk work plan.
     * The terminal result is accepted only when it returns a receipt bound to
     * the same execution, immutable manifest, diff, and ledger digest.
     */
    public Map<String, Object> performAnalysis(
            AiAnalysisRequest request,
            java.util.function.Consumer<Map<String, Object>> eventHandler,
            PolicyExecution policyExecution,
            String indexVersion,
            ImmutableExecutionManifest executionManifest,
            CoverageWorkPlan coverageWorkPlan)
            throws IOException, GeneralSecurityException {
        return performAnalysis(
                request,
                eventHandler,
                policyExecution,
                indexVersion,
                RagExecutionConfigV1.defaults(indexVersion),
                executionManifest,
                coverageWorkPlan);
    }

    public Map<String, Object> performAnalysis(
            AiAnalysisRequest request,
            java.util.function.Consumer<Map<String, Object>> eventHandler,
            PolicyExecution policyExecution,
            String indexVersion,
            RagExecutionConfigV1 ragContext,
            ImmutableExecutionManifest executionManifest,
            CoverageWorkPlan coverageWorkPlan)
            throws IOException, GeneralSecurityException {
        requireCandidateBinding(
                request, policyExecution, executionManifest, ragContext);
        requireMaterializedCandidateRequest(request);
        requireCandidateIndexVersion(indexVersion, executionManifest);
        requireEqual(indexVersion, ragContext.indexVersion(), "ragContext.indexVersion");
        requireCoverageWorkPlanBinding(coverageWorkPlan, executionManifest);
        return performAnalysisInternal(
                request,
                eventHandler,
                policyExecution,
                indexVersion,
                ragContext,
                executionManifest,
                coverageWorkPlan);
    }

    private Map<String, Object> performAnalysisInternal(
            AiAnalysisRequest request,
            java.util.function.Consumer<Map<String, Object>> eventHandler,
            PolicyExecution policyExecution,
            String indexVersion,
            RagExecutionConfigV1 ragContext,
            ImmutableExecutionManifest executionManifest,
            CoverageWorkPlan coverageWorkPlan)
            throws IOException, GeneralSecurityException {

        String jobId = UUID.randomUUID().toString();
        String eventQueueKey = "codecrow:analysis:events:" + jobId;

        try {
            log.info("Sending async analysis request to Redis queue (Job ID: {})", jobId);

            // Candidate jobs use an explicit transport schema so a restarted
            // worker cannot silently accept an incompatible outer envelope.
            // Legacy jobs retain their frozen pre-version shape during the
            // documented compatibility window.
            Map<String, Object> jobPayload = new LinkedHashMap<>();
            if (coverageWorkPlan != null) {
                jobPayload.put("schemaVersion", 2);
            }
            jobPayload.put("job_id", jobId);
            Map<String, Object> requestPayload = buildSerializableRequestPayload(
                    request,
                    policyExecution,
                    indexVersion,
                    ragContext,
                    executionManifest,
                    coverageWorkPlan);
            jobPayload.put("request", requestPayload);

            String jsonPayload = objectMapper.writeValueAsString(jobPayload);

            // Push the job to the Redis queue
            queueService.leftPush(JOBS_QUEUE_KEY, jsonPayload);

            // Set an expiration on the event queue to prevent orphaned keys if everything
            // crashes
            queueService.setExpiry(eventQueueKey, REVIEW_TIMEOUT_MINUTES + 1);

            long startTime = currentTimeMillis.getAsLong();
            long timeoutMillis = TimeUnit.MINUTES.toMillis(REVIEW_TIMEOUT_MINUTES);

            // Poll the event queue for progress or final result
            while (true) {
                if (currentTimeMillis.getAsLong() - startTime > timeoutMillis) {
                    throw new IOException(
                            "AI Analysis timed out after " + REVIEW_TIMEOUT_MINUTES + " minutes for Job: " + jobId);
                }

                String eventJson = queueService.rightPop(eventQueueKey, 5);

                if (eventJson == null) {
                    continue; // Timeout on BLPOP, continue to check overall timeout
                }

                Map<String, Object> event;
                try {
                    event = objectMapper.readValue(eventJson, Map.class);
                } catch (IOException parseError) {
                    if (executionManifest != null) {
                        throw new IOException(
                                "Candidate Redis event JSON is malformed and cannot prove execution identity",
                                parseError);
                    }
                    // Explicit legacy queues retain their historical best-effort
                    // tolerance for malformed progress entries.
                    log.warn("Failed to parse Redis event JSON: {}", parseError.getMessage());
                    continue;
                }

                try {
                    requireEventManifestBinding(event, executionManifest);
                    Object type = event.get("type");
                    Map<String, Object> finalResult = null;
                    Map<String, Object> controlResult = null;

                    if ("final".equals(type) || "result".equals(type)) {
                        Object res = event.get("result");
                        if (res instanceof Map) {
                            finalResult = (Map<String, Object>) res;
                        } else if (res != null) {
                            finalResult = Map.of("result", res);
                        }

                        if (finalResult == null) {
                            throw new IOException(
                                    "AI service returned final event without a valid result payload");
                        }
                        if (coverageWorkPlan != null) {
                            requireCoverageReceiptBinding(
                                    finalResult, coverageWorkPlan);
                        }
                        if (executionManifest != null) {
                            requireReturnedReviewApproach(
                                    finalResult, request.getReviewApproach());
                        }
                    } else if ("superseded".equals(type)) {
                        controlResult = requireSupersededControl(
                                event, executionManifest);
                    }

                    // Forward event to caller if handler provided
                    if (eventHandler != null) {
                        try {
                            eventHandler.accept(event);
                        } catch (Exception ex) {
                            if (executionManifest != null) {
                                throw new IOException(
                                        "Candidate event handler rejected an identity-bound event",
                                        ex);
                            }
                            log.warn("Event handler threw exception: {}", ex.getMessage());
                        }
                    }

                    if ("error".equals(type) || "failed".equals(type)) {
                        String errMsg = String.valueOf(event.get("message"));
                        throw new IOException("AI service returned error: " + errMsg);
                    }

                    if (controlResult != null) {
                        log.info(
                                "AI async job {} stopped because a newer head superseded it ({})",
                                jobId,
                                controlResult.get("computeState"));
                        return controlResult;
                    }

                    if ("final".equals(type) || "result".equals(type)) {
                        log.info("AI async job {} completed successfully", jobId);
                        return extractAndValidateAnalysisData(finalResult);
                    }
                } catch (IOException ex) {
                    throw ex; // Re-throw fatal IO exceptions
                } catch (Exception ex) {
                    if (executionManifest != null) {
                        throw new IOException(
                                "Failed to process candidate Redis event",
                                ex);
                    }
                    log.warn("Failed to process Redis event: {}", ex.getMessage(), ex);
                }
            }

        } catch (Exception e) {
            log.error("Failed to communicate with AI async queue", e);
            throw new IOException("AI queue communication failed: " + e.getMessage(), e);
        } finally {
            try {
                // Clean up the event queue if we exit early or successfully
                queueService.deleteKey(eventQueueKey);
            } catch (Exception ignored) {
            }
        }
    }

    private Map<String, Object> buildSerializableRequestPayload(
            AiAnalysisRequest request,
            PolicyExecution policyExecution,
            String indexVersion,
            RagExecutionConfigV1 ragContext,
            ImmutableExecutionManifest executionManifest,
            CoverageWorkPlan coverageWorkPlan) {
        Map<String, Object> payload = new LinkedHashMap<>();
        payload.put("projectId", request.getProjectId());
        payload.put("projectWorkspace", request.getProjectWorkspace());
        payload.put("projectNamespace", request.getProjectNamespace());
        payload.put("projectVcsWorkspace", request.getProjectVcsWorkspace());
        payload.put("projectVcsRepoSlug", request.getProjectVcsRepoSlug());
        payload.put("aiProvider", request.getAiProvider());
        payload.put("aiModel", request.getAiModel());
        payload.put("aiApiKey", request.getAiApiKey());
        payload.put("aiBaseUrl", request.getAiBaseUrl());
        payload.put("aiCustomParameters", parseAiCustomParameters(request.getAiCustomParameters()));
        payload.put("pullRequestId", request.getPullRequestId());
        payload.put("oAuthClient", request.getOAuthClient());
        payload.put("oAuthSecret", request.getOAuthSecret());
        payload.put("accessToken", request.getAccessToken());
        payload.put("maxAllowedTokens", request.getMaxAllowedTokens());
        payload.put("useLocalMcp", request.getUseLocalMcp());
        payload.put("useMcpTools", request.getUseMcpTools());
        payload.put("reviewApproach", request.getReviewApproach());
        if (request.getAgenticRepository() != null) {
            payload.put("agenticRepository", request.getAgenticRepository());
        }
        payload.put("analysisType", request.getAnalysisType());
        payload.put("vcsProvider", request.getVcsProvider());
        payload.put("prTitle", request.getPrTitle());
        payload.put("prDescription", request.getPrDescription());
        payload.put("taskContext", request.getTaskContext());
        payload.put("taskHistoryContext", request.getTaskHistoryContext());
        payload.put("changedFiles", request.getChangedFiles());
        payload.put("deletedFiles", request.getDeletedFiles());
        payload.put("diffSnippets", request.getDiffSnippets());
        payload.put("targetBranchName", request.getTargetBranchName());
        payload.put("sourceBranchName", request.getSourceBranchName());
        payload.put("rawDiff", request.getRawDiff());
        payload.put("analysisMode", request.getAnalysisMode());
        payload.put("deltaDiff", request.getDeltaDiff());
        payload.put("previousCommitHash", request.getPreviousCommitHash());
        payload.put("currentCommitHash", request.getCurrentCommitHash());
        payload.put("previousCodeAnalysisIssues", request.getPreviousCodeAnalysisIssues());
        payload.put("reconciliationFileContents", request.getReconciliationFileContents());
        if (request instanceof AiAnalysisRequestImpl impl) {
            payload.put("enrichmentData", impl.getEnrichmentData());
            payload.put("projectRules", impl.getProjectRules());
            if (impl.getEnrichmentData() != null
                    && impl.getEnrichmentData().reviewContext() != null) {
                payload.put("prAuthor", impl.getEnrichmentData().reviewContext().prAuthor());
            }
        }
        if (executionManifest != null) {
            payload.put("executionManifest", executionManifest);
        } else {
            payload.put("legacyCompatibility", Map.of(
                    "kind", "legacy",
                    "deadline", LEGACY_COMPATIBILITY_DEADLINE));
        }
        if (coverageWorkPlan != null) {
            payload.put("coverageLedger", coverageLedgerPayload(coverageWorkPlan));
        }
        if (policyExecution != null) {
            if (executionManifest == null) {
                payload.put("executionId", policyExecution.executionId());
            }
            payload.put("policyVersion", policyExecution.policyVersion());
            payload.put("executionMode", policyExecution.mode().name().toLowerCase(java.util.Locale.ROOT));
            payload.put("policySelectionReason",
                    policyExecution.selectionReason().name().toLowerCase(java.util.Locale.ROOT));
            payload.put("publicationAllowed", policyExecution.publicationAllowed());
        }
        if (indexVersion != null && !indexVersion.isBlank()) {
            payload.put("indexVersion", indexVersion);
        }
        if (ragContext != null) {
            payload.put("ragContext", ragContext);
        }
        resolveModelPricing(request).ifPresent(model -> {
            payload.put("inputPricePerMillion", model.getInputPricePerMillion());
            payload.put("outputPricePerMillion", model.getOutputPricePerMillion());
        });
        return payload;
    }

    private static void requireCandidateBinding(
            AiAnalysisRequest request,
            PolicyExecution policyExecution,
            ImmutableExecutionManifest executionManifest,
            RagExecutionConfigV1 ragContext) {
        Objects.requireNonNull(request, "request");
        if (executionManifest == null) {
            throw new IllegalArgumentException(
                    "executionManifest is required for the candidate v2 queue path");
        }
        if (policyExecution == null) {
            throw new IllegalArgumentException(
                    "policyExecution is required for the candidate v2 queue path");
        }
        Objects.requireNonNull(ragContext, "ragContext");
        ragContext.requireCompatibleBaseSha(executionManifest.baseSha());
        requireEqual(request.getProjectId(), executionManifest.projectId(), "projectId");
        requireEqual(request.getPullRequestId(), executionManifest.pullRequestId(), "pullRequestId");
        requireEqual(request.getBaseSha(), executionManifest.baseSha(), "baseSha");
        requireEqual(request.getHeadSha(), executionManifest.headSha(), "headSha");
        requireEqual(request.getMergeBaseSha(), executionManifest.mergeBaseSha(), "mergeBaseSha");
        requireEqual(
                request.getPreviousCommitHash(), executionManifest.baseSha(), "previousCommitHash");
        requireEqual(
                request.getCurrentCommitHash(), executionManifest.headSha(), "currentCommitHash");
        requireEqual(policyExecution.executionId(), executionManifest.executionId(), "executionId");
        requireEqual(policyExecution.policyVersion(), executionManifest.policyVersion(), "policyVersion");
        requireEqual(policyExecution.createdAt(), executionManifest.createdAt(), "createdAt");
        if (request.getAnalysisType()
                != org.rostilos.codecrow.core.model.codeanalysis.AnalysisType.PR_REVIEW) {
            throw new IllegalArgumentException(
                    "analysisType conflicts with executionManifest");
        }
        if (request.getAnalysisMode()
                != org.rostilos.codecrow.core.model.codeanalysis.AnalysisMode.FULL) {
            throw new IllegalArgumentException(
                    "analysisMode conflicts with executionManifest");
        }
        if (request.getDeltaDiff() != null) {
            throw new IllegalArgumentException(
                    "deltaDiff is not bound by executionManifest");
        }
        if (request.getPreviousCodeAnalysisIssues() != null
                && !request.getPreviousCodeAnalysisIssues().isEmpty()) {
            throw new IllegalArgumentException(
                    "previousCodeAnalysisIssues are not bound by executionManifest");
        }
        if (request.getDiffSnippets() != null && !request.getDiffSnippets().isEmpty()) {
            throw new IllegalArgumentException(
                    "diffSnippets are not bound by executionManifest");
        }
        PrEnrichmentDataDto.ReviewContext reviewContext =
                request instanceof AiAnalysisRequestImpl impl
                                && impl.getEnrichmentData() != null
                        ? impl.getEnrichmentData().reviewContext()
                        : null;
        if (reviewContext == null) {
            requireAbsent(request.getPrTitle(), "prTitle");
            requireAbsent(request.getPrDescription(), "prDescription");
            if (request.getTaskContext() != null && !request.getTaskContext().isEmpty()) {
                throw new IllegalArgumentException(
                        "taskContext is not bound by executionManifest");
            }
            requireAbsent(request.getTaskHistoryContext(), "taskHistoryContext");
            requireAbsent(request.getSourceBranchName(), "sourceBranchName");
            requireAbsent(request.getTargetBranchName(), "targetBranchName");
            if (request instanceof AiAnalysisRequestImpl impl) {
                requireAbsent(impl.getProjectRules(), "projectRules");
            }
        } else {
            requireEqual(request.getPrTitle(), reviewContext.prTitle(), "prTitle");
            requireEqual(
                    request.getPrDescription(),
                    reviewContext.prDescription(),
                    "prDescription");
            requireEqual(request.getTaskContext(), reviewContext.taskContext(), "taskContext");
            requireEqual(
                    request.getTaskHistoryContext(),
                    reviewContext.taskHistoryContext(),
                    "taskHistoryContext");
            requireEqual(
                    request.getSourceBranchName(),
                    reviewContext.sourceBranchName(),
                    "sourceBranchName");
            requireEqual(
                    request.getTargetBranchName(),
                    reviewContext.targetBranchName(),
                    "targetBranchName");
            requireEqual(
                    ((AiAnalysisRequestImpl) request).getProjectRules(),
                    reviewContext.projectRules(),
                    "projectRules");
        }
        org.rostilos.codecrow.core.model.project.config.ReviewApproach requestApproach =
                org.rostilos.codecrow.core.model.project.config.ReviewApproach.orDefault(
                        request.getReviewApproach());
        if (reviewContext == null
                || reviewContext.schemaVersion()
                == PrEnrichmentDataDto.LEGACY_REVIEW_CONTEXT_SCHEMA_VERSION) {
            if (requestApproach
                    != org.rostilos.codecrow.core.model.project.config.ReviewApproach.CLASSIC) {
                throw new IllegalArgumentException(
                        "AGENTIC review requires a manifest-bound reviewApproach");
            }
        } else {
            requireEqual(
                    requestApproach,
                    reviewContext.reviewApproach(),
                    "reviewApproach");
        }
        if (request.getUseMcpTools()) {
            throw new IllegalArgumentException(
                    "useMcpTools is not bound by executionManifest");
        }
        requireAgenticRepositoryBinding(request, executionManifest);

        String provider = requiredPart(request.getVcsProvider(), "vcsProvider")
                .toLowerCase(Locale.ROOT);
        String workspace = requiredPart(request.getProjectVcsWorkspace(), "projectVcsWorkspace");
        String repository = requiredPart(request.getProjectVcsRepoSlug(), "projectVcsRepoSlug");
        requireEqual(
                provider + ":" + workspace + "/" + repository,
                executionManifest.repositoryId(),
                "repositoryId");

        String rawDiff = Objects.requireNonNull(request.getRawDiff(), "rawDiff");
        executionManifest.verifyRawDiff(rawDiff.getBytes(StandardCharsets.UTF_8));
        if (request.getReconciliationFileContents() != null
                && !request.getReconciliationFileContents().isEmpty()) {
            throw new IllegalArgumentException(
                    "reconciliationFileContents are not bound by executionManifest");
        }
        ExecutionInputArtifactBundle observedInputs = ExecutionInputArtifactBundle.create(
                executionManifest.executionId(),
                executionManifest.headSha(),
                executionManifest.diffArtifactId(),
                rawDiff.getBytes(StandardCharsets.UTF_8),
                request instanceof AiAnalysisRequestImpl impl
                        ? impl.getEnrichmentData()
                        : null,
                ragContext,
                executionManifest.artifactSchemaVersion(),
                executionManifest.diffArtifactProducer(),
                executionManifest.diffArtifactProducerVersion());
        requireChangedFileInventory(request, observedInputs);
        if (!executionManifest.inputArtifacts().equals(observedInputs.entries())) {
            throw new IllegalArgumentException(
                    "candidate input artifacts conflict with executionManifest");
        }
    }

    private static void requireAgenticRepositoryBinding(
            AiAnalysisRequest request,
            ImmutableExecutionManifest executionManifest) {
        AgenticRepositoryArchiveV1 repository = request.getAgenticRepository();
        org.rostilos.codecrow.core.model.project.config.ReviewApproach approach =
                request.getReviewApproach();
        if (approach
                == org.rostilos.codecrow.core.model.project.config.ReviewApproach.AGENTIC) {
            if (repository == null) {
                throw new IllegalArgumentException(
                        "AGENTIC review requires agenticRepository");
            }
            requireEqual(
                    repository.snapshotSha(),
                    executionManifest.headSha(),
                    "agenticRepository.snapshotSha");
        } else if (repository != null) {
            throw new IllegalArgumentException(
                    "CLASSIC review cannot carry agenticRepository");
        }
    }

    private static void requireMaterializedCandidateRequest(AiAnalysisRequest request) {
        if (request == null || request.getClass() != AiAnalysisRequestImpl.class) {
            throw new IllegalArgumentException(
                    "candidate queue path requires a materialized immutable AiAnalysisRequestImpl");
        }
    }

    private static void requireChangedFileInventory(
            AiAnalysisRequest request,
            ExecutionInputArtifactBundle observedInputs) {
        List<String> changedFiles = request.getChangedFiles() == null
                ? List.of()
                : request.getChangedFiles();
        Set<String> requestedPaths = new HashSet<>(changedFiles);
        if (requestedPaths.size() != changedFiles.size()
                || requestedPaths.stream().anyMatch(
                        path -> path == null || path.isBlank() || path.indexOf('\0') >= 0)) {
            throw new IllegalArgumentException(
                    "changedFiles contain an invalid or duplicate path");
        }
        Set<String> manifestBoundPaths = new HashSet<>();
        for (var payload : observedInputs.artifacts()) {
            if (payload.entry().kind()
                    == org.rostilos.codecrow.analysisengine.execution.ArtifactManifestEntry.Kind.SOURCE_FILE) {
                manifestBoundPaths.add(payload.entry().contentKey());
            }
        }
        if (request instanceof AiAnalysisRequestImpl impl
                && impl.getEnrichmentData() != null
                && impl.getEnrichmentData().fileContents() != null) {
            impl.getEnrichmentData().fileContents().stream()
                    .filter(java.util.Objects::nonNull)
                    .filter(file -> file.skipped())
                    .map(org.rostilos.codecrow.analysisengine.dto.request.ai.enrichment.FileContentDto::path)
                    .forEach(manifestBoundPaths::add);
        }
        if (!manifestBoundPaths.equals(requestedPaths)) {
            throw new IllegalArgumentException(
                    "changedFiles conflict with manifest-bound source inventory");
        }
    }

    private static void requireAbsent(String value, String field) {
        if (value != null && !value.isBlank()) {
            throw new IllegalArgumentException(
                    field + " is not bound by executionManifest");
        }
    }

    private static void requireCandidateIndexVersion(
            String indexVersion,
            ImmutableExecutionManifest executionManifest) {
        String expectedExactIndex = "rag-commit-" + executionManifest.baseSha();
        if (!"rag-disabled".equals(indexVersion)
                && !expectedExactIndex.equals(indexVersion)) {
            throw new IllegalArgumentException(
                    "candidate indexVersion must be disabled or match manifest baseSha");
        }
    }

    private static Map<String, Object> coverageLedgerPayload(
            CoverageWorkPlan coverageWorkPlan) {
        Map<String, Object> payload = new LinkedHashMap<>();
        payload.put("schemaVersion", coverageWorkPlan.schemaVersion());
        payload.put("executionId", coverageWorkPlan.executionId());
        payload.put(
                "artifactManifestDigest",
                coverageWorkPlan.artifactManifestDigest());
        payload.put("diffDigest", coverageWorkPlan.diffDigest());
        payload.put("diffByteLength", coverageWorkPlan.diffByteLength());
        payload.put("anchorCount", coverageWorkPlan.anchors().size());
        payload.put("anchors", coverageWorkPlan.anchors());
        payload.put("ledgerDigest", coverageWorkPlan.ledgerDigest());
        return payload;
    }

    private static void requireCoverageWorkPlanBinding(
            CoverageWorkPlan coverageWorkPlan,
            ImmutableExecutionManifest executionManifest) {
        if (coverageWorkPlan == null) {
            throw new IllegalArgumentException(
                    "coverageWorkPlan is required for the v2 queue path");
        }
        requireEqual(
                coverageWorkPlan.executionId(),
                executionManifest.executionId(),
                "coverageWorkPlan.executionId");
        requireEqual(
                coverageWorkPlan.artifactManifestDigest(),
                executionManifest.artifactManifestDigest(),
                "coverageWorkPlan.artifactManifestDigest");
        requireEqual(
                coverageWorkPlan.diffDigest(),
                executionManifest.diffDigest(),
                "coverageWorkPlan.diffDigest");
        if (coverageWorkPlan.diffByteLength() != executionManifest.diffByteLength()) {
            throw new IllegalArgumentException(
                    "coverageWorkPlan.diffByteLength conflicts with executionManifest");
        }
        if (coverageWorkPlan.schemaVersion() != 1) {
            throw new IllegalArgumentException(
                    "coverageWorkPlan.schemaVersion must be 1");
        }
        if (coverageWorkPlan.ledgerDigest() == null
                || !coverageWorkPlan.ledgerDigest().matches("[0-9a-f]{64}")) {
            throw new IllegalArgumentException(
                    "coverageWorkPlan.ledgerDigest must be a lowercase SHA-256");
        }
        if (coverageWorkPlan.anchors() == null) {
            throw new IllegalArgumentException(
                    "coverageWorkPlan.anchors are required");
        }
        String previousAnchorId = null;
        Set<String> anchorIds = new HashSet<>();
        for (CoverageAnchor anchor : coverageWorkPlan.anchors()) {
            if (anchor == null) {
                throw new IllegalArgumentException(
                        "coverageWorkPlan contains a null anchor");
            }
            requireEqual(
                    anchor.executionId(),
                    executionManifest.executionId(),
                    "coverageWorkPlan.anchor.executionId");
            if (!anchorIds.add(anchor.anchorId())) {
                throw new IllegalArgumentException(
                        "coverageWorkPlan contains a duplicate anchorId");
            }
            if (previousAnchorId != null
                    && previousAnchorId.compareTo(anchor.anchorId()) >= 0) {
                throw new IllegalArgumentException(
                        "coverageWorkPlan anchors are not in canonical anchorId order");
            }
            previousAnchorId = anchor.anchorId();
        }
    }

    private static void requireCoverageReceiptBinding(
            Map<String, Object> finalResult,
            CoverageWorkPlan coverageWorkPlan) throws IOException {
        Object value = finalResult.get("coverageReceipt");
        if (!(value instanceof Map<?, ?> receipt)) {
            throw new IOException(
                    "Candidate v2 result is missing a coverageReceipt");
        }
        requireReceiptEqual(
                receipt.get("schemaVersion"),
                coverageWorkPlan.schemaVersion(),
                "schemaVersion");
        requireReceiptEqual(
                receipt.get("executionId"),
                coverageWorkPlan.executionId(),
                "executionId");
        requireReceiptEqual(
                receipt.get("artifactManifestDigest"),
                coverageWorkPlan.artifactManifestDigest(),
                "artifactManifestDigest");
        requireReceiptEqual(
                receipt.get("diffDigest"),
                coverageWorkPlan.diffDigest(),
                "diffDigest");
        requireReceiptEqual(
                receipt.get("diffByteLength"),
                coverageWorkPlan.diffByteLength(),
                "diffByteLength");
        requireReceiptEqual(
                receipt.get("ledgerDigest"),
                coverageWorkPlan.ledgerDigest(),
                "ledgerDigest");
        if (!(receipt.get("dispositions") instanceof List<?> values)) {
            throw new IOException(
                    "Candidate v2 coverageReceipt dispositions are missing or malformed");
        }
        Map<String, CoverageAnchor> anchorsById = new LinkedHashMap<>();
        coverageWorkPlan.anchors().forEach(
                anchor -> anchorsById.put(anchor.anchorId(), anchor));
        Map<String, CoverageDisposition> dispositionsById = new LinkedHashMap<>();
        for (Object valueItem : values) {
            if (!(valueItem instanceof Map<?, ?> item)) {
                throw new IOException(
                        "Candidate v2 coverageReceipt contains a malformed disposition");
            }
            Object rawAnchorId = item.get("anchorId");
            Object rawState = item.get("state");
            Object rawReason = item.get("reasonCode");
            if (!(rawAnchorId instanceof String anchorId)
                    || !(rawState instanceof String stateName)
                    || (rawReason != null && !(rawReason instanceof String))) {
                throw new IOException(
                        "Candidate v2 coverageReceipt contains a malformed disposition");
            }

            CoverageAnchorState dispositionState;
            CoverageDisposition disposition;
            try {
                dispositionState = CoverageAnchorState.valueOf(stateName);
                disposition = new CoverageDisposition(
                        anchorId, dispositionState, (String) rawReason);
            } catch (IllegalArgumentException | NullPointerException error) {
                throw new IOException(
                        "Candidate v2 coverageReceipt contains a malformed disposition",
                        error);
            }
            if (dispositionsById.putIfAbsent(anchorId, disposition) != null) {
                throw new IOException(
                        "Candidate v2 coverageReceipt contains a duplicate anchorId");
            }
            CoverageAnchor anchor = anchorsById.get(anchorId);
            if (anchor == null) {
                throw new IOException(
                        "Candidate v2 coverageReceipt contains an unknown anchorId");
            }
            if (!anchor.initialState().open()) {
                if (dispositionState != anchor.initialState()
                        || !Objects.equals(disposition.reasonCode(), anchor.reasonCode())) {
                    throw new IOException(
                            "Candidate v2 coverageReceipt replaced an immutable disposition");
                }
            } else if (dispositionState.open()
                    || dispositionState == CoverageAnchorState.POLICY_EXCLUDED
                    || dispositionState == CoverageAnchorState.DELETED_RECORDED) {
                throw new IOException(
                        "Candidate v2 coverageReceipt contains a nonterminal disposition");
            }
        }
        if (values.size() != coverageWorkPlan.anchors().size()) {
            throw new IOException(
                    "Candidate v2 coverageReceipt must account for every anchor");
        }

        List<CoverageDisposition> dispositions = List.copyOf(
                dispositionsById.values());
        CoverageCounts counts = CoverageCounts.fromDispositions(dispositions);
        requireReceiptEqual(receipt.get("total"), counts.inventory(), "total");
        requireReceiptEqual(receipt.get("pending"), counts.pending(), "pending");
        requireReceiptEqual(
                receipt.get("ownerPending"), counts.ownerPending(), "ownerPending");
        requireReceiptEqual(receipt.get("examined"), counts.examined(), "examined");
        requireReceiptEqual(
                receipt.get("incomplete"), counts.incomplete(), "incomplete");
        requireReceiptEqual(
                receipt.get("unsupported"), counts.unsupported(), "unsupported");
        requireReceiptEqual(receipt.get("failed"), counts.failed(), "failed");
        requireReceiptEqual(
                receipt.get("policyExcluded"),
                counts.policyExcluded(),
                "policyExcluded");
        requireReceiptEqual(
                receipt.get("deletedRecorded"),
                counts.deletedRecorded(),
                "deletedRecorded");

        List<CoverageDisposition> mandatory = coverageWorkPlan.anchors().stream()
                .filter(CoverageAnchor::mandatory)
                .map(anchor -> dispositionsById.get(anchor.anchorId()))
                .toList();
        CoverageAnalysisState expectedState;
        if (mandatory.isEmpty()) {
            expectedState = CoverageAnalysisState.EMPTY;
        } else if (mandatory.stream().allMatch(
                item -> item.state().satisfiesMandatoryCoverage())) {
            expectedState = CoverageAnalysisState.COMPLETE;
        } else if (mandatory.stream().noneMatch(
                item -> item.state().satisfiesMandatoryCoverage())
                && mandatory.stream().anyMatch(
                        item -> item.state() == CoverageAnchorState.FAILED)) {
            expectedState = CoverageAnalysisState.FAILED;
        } else {
            expectedState = CoverageAnalysisState.PARTIAL;
        }
        requireReceiptEqual(
                receipt.get("analysisState"),
                expectedState.name(),
                "analysisState");
    }

    private static void requireReceiptEqual(
            Object observed,
            Object expected,
            String field) throws IOException {
        BigInteger observedInteger = strictInteger(observed);
        BigInteger expectedInteger = strictInteger(expected);
        boolean matches = observed instanceof Number || expected instanceof Number
                ? observedInteger != null && observedInteger.equals(expectedInteger)
                : Objects.equals(observed, expected);
        if (!matches) {
            throw new IOException(
                    "Candidate v2 coverageReceipt " + field + " conflicts with coverage ledger");
        }
    }

    private static BigInteger strictInteger(Object value) {
        if (value instanceof BigInteger integer) {
            return integer;
        }
        if (value instanceof Byte
                || value instanceof Short
                || value instanceof Integer
                || value instanceof Long) {
            return BigInteger.valueOf(((Number) value).longValue());
        }
        return null;
    }

    private static void requireEventManifestBinding(
            Map<String, Object> event,
            ImmutableExecutionManifest executionManifest) throws IOException {
        if (executionManifest == null) {
            return;
        }
        if (event == null) {
            throw new IOException(
                    "AI event is missing and cannot prove execution identity");
        }
        requireEventEqual(
                event.get("executionId"), executionManifest.executionId(), "executionId");
        requireEventEqual(
                event.get("artifactManifestDigest"),
                executionManifest.artifactManifestDigest(),
                "artifactManifestDigest");
    }

    private static Map<String, Object> requireSupersededControl(
            Map<String, Object> event,
            ImmutableExecutionManifest executionManifest) throws IOException {
        if (executionManifest == null) {
            throw new IOException(
                    "AI superseded control requires an immutable execution manifest");
        }
        Object reasonCode = event.get("reasonCode");
        if (!"latest_head_advanced".equals(reasonCode)) {
            throw new IOException(
                    "AI superseded control reasonCode is missing or invalid");
        }
        Object computeState = event.get("computeState");
        if (!(computeState instanceof String state)
                || !Set.of(
                        "not_started",
                        "cancelled",
                        "completed_discarded")
                        .contains(state)) {
            throw new IOException(
                    "AI superseded control computeState is missing or invalid");
        }
        return Map.of(
                "status", "superseded",
                "reason", "latest_head_advanced",
                "computeState", state);
    }

    private static void requireEventEqual(
            Object observed,
            String expected,
            String field) throws IOException {
        if (!(observed instanceof String value)) {
            throw new IOException(
                    "AI event " + field + " is missing or malformed");
        }
        if (!expected.equals(value)) {
            throw new IOException(
                    "AI event " + field + " conflicts with executionManifest");
        }
    }

    private static String requiredPart(String value, String field) {
        if (value == null || value.isBlank()) {
            throw new IllegalArgumentException(field + " is required for the candidate queue path");
        }
        return value;
    }

    private static void requireEqual(Object observed, Object expected, String field) {
        if (!Objects.equals(observed, expected)) {
            throw new IllegalArgumentException(field + " conflicts with executionManifest");
        }
    }

    java.util.Optional<LlmModel> resolveModelPricing(AiAnalysisRequest request) {
        if (llmModelRepository == null || request.getAiProvider() == null
                || request.getAiModel() == null || request.getAiModel().isBlank()) {
            return java.util.Optional.empty();
        }
        try {
            return llmModelRepository.findByProviderKeyAndModelId(
                            request.getAiProvider(), request.getAiModel())
                    .filter(model -> model.getInputPricePerMillion() != null)
                    .filter(model -> model.getOutputPricePerMillion() != null);
        } catch (RuntimeException error) {
            log.warn("Model pricing lookup unavailable: {}", error.getClass().getSimpleName());
            return java.util.Optional.empty();
        }
    }

    private Map<String, Object> parseAiCustomParameters(String rawParameters) {
        if (rawParameters == null || rawParameters.isBlank()) {
            return null;
        }
        try {
            return objectMapper.readValue(rawParameters, new TypeReference<>() {
            });
        } catch (IOException e) {
            throw new IllegalArgumentException("Invalid AI custom parameters JSON", e);
        }
    }

    private Map<String, Object> extractAndValidateAnalysisData(Map<String, Object> result) throws IOException {
        try {
            if (result == null) {
                throw new IOException("Missing 'result' field in AI response");
            }

            // Check for error response from Inference Orchestrator
            Object errorFlag = result.get("error");
            if (Boolean.TRUE.equals(errorFlag) || "true".equals(String.valueOf(errorFlag))) {
                String errorMessage = result.get("error_message") != null
                        ? String.valueOf(result.get("error_message"))
                        : String.valueOf(result.get("comment"));
                throw new IOException("Analysis failed: " + errorMessage);
            }

            if (!result.containsKey("comment") || !result.containsKey("issues")) {
                throw new IOException("Analysis data missing required fields: 'comment' and/or 'issues'");
            }

            // Log issue count - handle both List and Map formats
            Object issues = result.get("issues");
            int issueCount = 0;
            if (issues instanceof List) {
                issueCount = ((List<?>) issues).size();
            } else if (issues instanceof Map) {
                issueCount = ((Map<?, ?>) issues).size();
            }
            log.info("Successfully extracted analysis data with {} issues", issueCount);

            return result;

        } catch (ClassCastException e) {
            throw new IOException("Invalid AI response structure: " + e.getMessage(), e);
        }
    }

    private static void requireReturnedReviewApproach(
            Map<String, Object> result,
            org.rostilos.codecrow.core.model.project.config.ReviewApproach expected)
            throws IOException {
        Object observed = result.get("reviewApproach");
        if (observed == null || !expected.name().equals(String.valueOf(observed))) {
            throw new IOException(
                    "AI result reviewApproach conflicts with the queued review approach");
        }
    }
}
