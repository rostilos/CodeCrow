package org.rostilos.codecrow.analysisengine.aiclient;

import com.fasterxml.jackson.core.type.TypeReference;
import com.fasterxml.jackson.databind.ObjectMapper;
import org.rostilos.codecrow.analysisengine.dto.request.ai.AiAnalysisRequest;
import org.rostilos.codecrow.analysisengine.dto.request.ai.AiAnalysisRequestImpl;
import org.rostilos.codecrow.analysisengine.util.PromptDryRunMode;
import org.slf4j.Logger;
import org.slf4j.LoggerFactory;
import org.springframework.beans.factory.annotation.Autowired;
import org.springframework.beans.factory.annotation.Qualifier;
import org.rostilos.codecrow.queue.RedisQueueService;
import org.rostilos.codecrow.core.model.rag.RagBranchIndexGenerationStatus;
import org.rostilos.codecrow.core.persistence.repository.rag.RagBranchIndexGenerationRepository;
import org.rostilos.codecrow.core.persistence.repository.rag.RagBranchIndexRepository;
import org.springframework.stereotype.Service;
import org.springframework.web.client.RestTemplate;

import java.io.IOException;
import java.security.GeneralSecurityException;
import java.time.OffsetDateTime;
import java.util.LinkedHashMap;
import java.util.List;
import java.util.Map;
import java.util.UUID;
import java.util.concurrent.TimeUnit;

/**
 * Client for communicating with the AI analysis service (Inference
 * Orchestrator).
 * Uses an async queue architecture backed by Redis via codecrow-queue.
 * Sends one mode-aware review request; token-safe file batching is handled by
 * the Python multi-stage pipeline's Stage 1.
 */
@Service
public class AiAnalysisClient {
    private static final Logger log = LoggerFactory.getLogger(AiAnalysisClient.class);

    private final RedisQueueService queueService;
    private final ObjectMapper objectMapper;

    @Autowired(required = false)
    private RagBranchIndexGenerationRepository branchGenerationRepository;

    @Autowired(required = false)
    private RagBranchIndexRepository branchIndexRepository;

    static final String INACTIVITY_TIMEOUT_MINUTES_KEY =
            "ANALYSIS_QUEUE_INACTIVITY_TIMEOUT_MINUTES";
    static final String ADMISSION_TIMEOUT_MINUTES_KEY =
            "ANALYSIS_QUEUE_ADMISSION_TIMEOUT_MINUTES";
    static final String CONSUMER_HEARTBEAT_KEY =
            "codecrow:analysis:consumer:heartbeat";
    private static final long DEFAULT_INACTIVITY_TIMEOUT_MINUTES = 15L;
    private static final long DEFAULT_ADMISSION_TIMEOUT_MINUTES = 5L;

    private final long inactivityTimeoutMillis;
    private final long admissionTimeoutMillis;

    @Autowired
    public AiAnalysisClient(
            @Qualifier("aiRestTemplate") RestTemplate restTemplate,
            RedisQueueService queueService,
            ObjectMapper objectMapper) {
        this(
                restTemplate,
                queueService,
                objectMapper,
                resolveInactivityTimeoutMillis(),
                resolveAdmissionTimeoutMillis());
    }

    AiAnalysisClient(
            RestTemplate restTemplate,
            RedisQueueService queueService,
            ObjectMapper objectMapper,
            long inactivityTimeoutMillis) {
        this(
                restTemplate,
                queueService,
                objectMapper,
                inactivityTimeoutMillis,
                resolveAdmissionTimeoutMillis());
    }

    AiAnalysisClient(
            RestTemplate restTemplate,
            RedisQueueService queueService,
            ObjectMapper objectMapper,
            long inactivityTimeoutMillis,
            long admissionTimeoutMillis) {
        // restTemplate kept in constructor for backward compatibility but no longer
        // used
        this.queueService = queueService;
        this.objectMapper = objectMapper;
        if (inactivityTimeoutMillis <= 0) {
            throw new IllegalArgumentException("Review queue inactivity timeout must be positive");
        }
        if (admissionTimeoutMillis <= 0) {
            throw new IllegalArgumentException("Review queue admission timeout must be positive");
        }
        this.inactivityTimeoutMillis = inactivityTimeoutMillis;
        this.admissionTimeoutMillis = admissionTimeoutMillis;
    }

    public Map<String, Object> performAnalysis(AiAnalysisRequest request)
            throws IOException, GeneralSecurityException {
        return performAnalysis(request, null);
    }

    public Map<String, Object> performAnalysis(AiAnalysisRequest request,
            java.util.function.Consumer<Map<String, Object>> eventHandler)
            throws IOException, GeneralSecurityException {

        String jobId = UUID.randomUUID().toString();
        String eventQueueKey = "codecrow:analysis:events:" + jobId;
        String jobsQueueKey = "codecrow:analysis:jobs";
        String jsonPayload = null;

        try {
            log.info("Sending async analysis request to Redis queue (Job ID: {})", jobId);

            if (!queueService.hasKey(CONSUMER_HEARTBEAT_KEY)) {
                throw new IOException(
                        "Inference Orchestrator is unavailable: no live review queue consumer");
            }

            // Wrap the request with the jobId
            boolean promptDryRun = PromptDryRunMode.isEnabledForProject(request.getProjectId());
            Map<String, Object> requestPayload = buildSerializableRequestPayload(request);
            requestPayload.put("promptDryRun", promptDryRun);
            if (promptDryRun) {
                requestPayload.put("promptDryRunId", jobId);
                requestPayload.put("aiApiKey", "dry-run-provider-disabled");
                requestPayload.put("oAuthClient", null);
                requestPayload.put("oAuthSecret", null);
                requestPayload.put("accessToken", null);
                log.warn(
                        "Prompt dry run enabled for project {} (Job ID: {}): "
                                + "the review LLM will not be called",
                        request.getProjectId(), jobId);
            }

            Map<String, Object> jobPayload = Map.of(
                    "job_id", jobId,
                    "request", requestPayload);

            jsonPayload = objectMapper.writeValueAsString(jobPayload);

            // Push the job to the Redis queue
            queueService.leftPush(jobsQueueKey, jsonPayload);

            // Set an expiration on the event queue to prevent orphaned keys if everything
            // crashes
            long eventTtlMinutes = Math.max(
                    2L,
                    (long) Math.ceil((double) inactivityTimeoutMillis
                            / TimeUnit.MINUTES.toMillis(1)) + 1L);
            queueService.setExpiry(eventQueueKey, eventTtlMinutes);

            long lastActivityTime = System.currentTimeMillis();
            long queuedAt = lastActivityTime;
            boolean workerAcknowledged = false;

            // Poll the event queue for progress or final result
            while (true) {
                long now = System.currentTimeMillis();
                if (!workerAcknowledged) {
                    if (!queueService.hasKey(CONSUMER_HEARTBEAT_KEY)) {
                        throw new IOException(
                                "Inference Orchestrator became unavailable before "
                                        + "the review job was admitted");
                    }
                    if (now - queuedAt > admissionTimeoutMillis) {
                        throw new IOException(
                                "Review was not admitted by Inference Orchestrator within "
                                        + TimeUnit.MILLISECONDS.toSeconds(admissionTimeoutMillis)
                                        + " seconds");
                    }
                }
                if (now - lastActivityTime > inactivityTimeoutMillis) {
                    if (queueService.listContains(jobsQueueKey, jsonPayload)) {
                        // Queue membership is durable ownership, but only a fresh
                        // consumer heartbeat proves that capacity can eventually
                        // admit it. Admission itself remains time-bounded.
                        lastActivityTime = System.currentTimeMillis();
                        forwardEvent(eventHandler, Map.of(
                                "type", "status",
                                "state", "queued",
                                "message", "Review is waiting for worker capacity"));
                        continue;
                    }
                    throw new IOException(
                            "AI Analysis produced no worker activity for "
                                    + inactivityTimeoutMillis + "ms for Job: " + jobId);
                }

                String eventJson = queueService.rightPop(eventQueueKey, 5);

                if (eventJson == null) {
                    continue; // Timeout on BRPOP, continue to check inactivity
                }
                lastActivityTime = System.currentTimeMillis();
                workerAcknowledged = true;

                try {
                    Map<String, Object> event = objectMapper.readValue(eventJson, Map.class);
                    Object type = event.get("type");

                    if ("error".equals(type) || "failed".equals(type)) {
                        forwardEvent(eventHandler, event);
                        String errMsg = String.valueOf(event.get("message"));
                        throw new IOException("AI service returned error: " + errMsg);
                    }

                    if ("final".equals(type) || "result".equals(type)) {
                        Object res = event.get("result");
                        Map<String, Object> finalResult = null;
                        if (res instanceof Map) {
                            finalResult = (Map<String, Object>) res;
                        } else if (res != null) {
                            finalResult = Map.of("result", res);
                        }

                        if (finalResult != null) {
                            log.info("AI async job {} completed successfully", jobId);
                            if (isPromptDryRunResult(finalResult)) {
                                return finalResult;
                            }
                            return extractAndValidateAnalysisData(finalResult);
                        } else {
                            throw new IOException("AI service returned final event without a valid result payload");
                        }
                    }

                    // Terminal result envelopes do not carry a progress message. Forwarding
                    // them made the UI append a misleading trailing "Processing..." event
                    // after the orchestrator had already completed.
                    forwardEvent(eventHandler, event);
                } catch (IOException ex) {
                    throw ex; // Re-throw fatal IO exceptions
                } catch (Exception ex) {
                    log.warn("Failed to parse Redis event JSON: {}", ex.getMessage(), ex);
                }
            }

        } catch (Exception e) {
            log.error("Failed to communicate with AI async queue", e);
            throw new IOException("AI queue communication failed: " + e.getMessage(), e);
        } finally {
            if (jsonPayload != null) {
                try {
                    queueService.removeFromList(jobsQueueKey, jsonPayload);
                } catch (Exception cleanupError) {
                    log.warn(
                            "Failed to remove analysis job {} from the pending queue: {}",
                            jobId,
                            cleanupError.getMessage());
                }
            }
            try {
                // Clean up the event queue if we exit early or successfully
                queueService.deleteKey(eventQueueKey);
            } catch (Exception cleanupError) {
                log.warn(
                        "Failed to delete analysis event queue {}: {}",
                        eventQueueKey,
                        cleanupError.getMessage());
            }
        }
    }

    private void forwardEvent(
            java.util.function.Consumer<Map<String, Object>> eventHandler,
            Map<String, Object> event) {
        if (eventHandler == null) {
            return;
        }
        try {
            eventHandler.accept(event);
        } catch (Exception ex) {
            log.warn("Event handler threw exception: {}", ex.getMessage());
        }
    }

    private static long resolveInactivityTimeoutMillis() {
        return resolvePositiveMinutes(
                INACTIVITY_TIMEOUT_MINUTES_KEY,
                DEFAULT_INACTIVITY_TIMEOUT_MINUTES);
    }

    private static long resolveAdmissionTimeoutMillis() {
        return resolvePositiveMinutes(
                ADMISSION_TIMEOUT_MINUTES_KEY,
                DEFAULT_ADMISSION_TIMEOUT_MINUTES);
    }

    private static long resolvePositiveMinutes(String key, long defaultMinutes) {
        String configured = System.getProperty(key);
        if (configured == null || configured.isBlank()) {
            configured = System.getenv(key);
        }
        if (configured == null || configured.isBlank()) {
            return TimeUnit.MINUTES.toMillis(defaultMinutes);
        }
        try {
            long minutes = Long.parseLong(configured.trim());
            if (minutes <= 0) {
                throw new IllegalArgumentException(key + " must be a positive integer");
            }
            return TimeUnit.MINUTES.toMillis(minutes);
        } catch (NumberFormatException error) {
            throw new IllegalArgumentException(
                    key + " must be a positive integer",
                    error);
        }
    }

    public static boolean isPromptDryRunResult(Map<String, Object> result) {
        return result != null && Boolean.TRUE.equals(result.get("dryRun"));
    }

    private Map<String, Object> buildSerializableRequestPayload(AiAnalysisRequest request) {
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
        payload.put("ragEnabled", request.getRagEnabled());
        payload.put("analysisType", request.getAnalysisType());
        payload.put("vcsProvider", request.getVcsProvider());
        payload.put("vcsBaseUrl", request.getVcsBaseUrl());
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
        payload.put("baseCommitHash", request.getBaseCommitHash());
        if (request.getRagEnabled()
                && branchGenerationRepository != null
                && branchIndexRepository != null
                && request.getProjectId() != null
                && request.getTargetBranchName() != null
                && request.getBaseCommitHash() != null) {
            int accessed = branchIndexRepository.markAccessedIfUnclaimed(
                    request.getProjectId(), request.getTargetBranchName(), OffsetDateTime.now());
            if (accessed > 0) {
                branchGenerationRepository.findAvailableExactGeneration(
                            request.getProjectId(),
                            request.getTargetBranchName(),
                            request.getBaseCommitHash(),
                            List.of(
                                    RagBranchIndexGenerationStatus.ACTIVE,
                                    RagBranchIndexGenerationStatus.SUPERSEDED))
                    .stream()
                    .findFirst()
                    .ifPresent(generation -> {
                        payload.put("ragCollectionTarget", generation.getCollectionName());
                        payload.put("ragBaseGenerationManifestSha256",
                                generation.getManifestDigest());
                    });
            }
        }
        payload.put("previousCodeAnalysisIssues", request.getPreviousCodeAnalysisIssues());
        payload.put("reconciliationFileContents", request.getReconciliationFileContents());
        payload.put("projectCapabilities", request.getProjectCapabilities());
        if (request instanceof AiAnalysisRequestImpl impl) {
            payload.put("enrichmentData", impl.getEnrichmentData());
            payload.put("projectRules", impl.getProjectRules());
        }
        return payload;
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
}
