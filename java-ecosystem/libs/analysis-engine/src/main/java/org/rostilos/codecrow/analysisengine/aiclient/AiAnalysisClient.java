package org.rostilos.codecrow.analysisengine.aiclient;

import com.fasterxml.jackson.core.type.TypeReference;
import com.fasterxml.jackson.databind.ObjectMapper;
import org.rostilos.codecrow.analysisengine.dto.request.ai.AiAnalysisRequest;
import org.rostilos.codecrow.analysisengine.dto.request.ai.AiAnalysisRequestImpl;
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
import java.security.GeneralSecurityException;
import java.util.LinkedHashMap;
import java.util.List;
import java.util.Map;
import java.util.UUID;
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

    public AiAnalysisClient(
            @Qualifier("aiRestTemplate") RestTemplate restTemplate,
            RedisQueueService queueService,
            ObjectMapper objectMapper) {
        this(restTemplate, queueService, objectMapper, null, System::currentTimeMillis);
    }

    @Autowired
    public AiAnalysisClient(
            @Qualifier("aiRestTemplate") RestTemplate restTemplate,
            RedisQueueService queueService,
            ObjectMapper objectMapper,
            LlmModelRepository llmModelRepository) {
        this(restTemplate, queueService, objectMapper, llmModelRepository, System::currentTimeMillis);
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

        String jobId = UUID.randomUUID().toString();
        String eventQueueKey = "codecrow:analysis:events:" + jobId;
        String jobsQueueKey = "codecrow:analysis:jobs";

        try {
            log.info("Sending async analysis request to Redis queue (Job ID: {})", jobId);

            // Wrap the request with the jobId
            Map<String, Object> jobPayload = Map.of(
                    "job_id", jobId,
                    "request", buildSerializableRequestPayload(request, policyExecution, indexVersion));

            String jsonPayload = objectMapper.writeValueAsString(jobPayload);

            // Push the job to the Redis queue
            queueService.leftPush(jobsQueueKey, jsonPayload);

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
                    // Progress queues are long-lived and may retain a malformed or
                    // partially written non-terminal event. Ignore that one event;
                    // explicit error/final events below remain fatal and validated.
                    log.warn("Failed to parse Redis event JSON: {}", parseError.getMessage());
                    continue;
                }

                try {

                    // Forward event to caller if handler provided
                    if (eventHandler != null) {
                        try {
                            eventHandler.accept(event);
                        } catch (Exception ex) {
                            log.warn("Event handler threw exception: {}", ex.getMessage());
                        }
                    }

                    Object type = event.get("type");

                    if ("error".equals(type) || "failed".equals(type)) {
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
                            return extractAndValidateAnalysisData(finalResult);
                        } else {
                            throw new IOException("AI service returned final event without a valid result payload");
                        }
                    }
                } catch (IOException ex) {
                    throw ex; // Re-throw fatal IO exceptions
                } catch (Exception ex) {
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
            String indexVersion) {
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
        }
        if (policyExecution != null) {
            payload.put("executionId", policyExecution.executionId());
            payload.put("policyVersion", policyExecution.policyVersion());
            payload.put("executionMode", policyExecution.mode().name().toLowerCase(java.util.Locale.ROOT));
            payload.put("policySelectionReason",
                    policyExecution.selectionReason().name().toLowerCase(java.util.Locale.ROOT));
            payload.put("publicationAllowed", policyExecution.publicationAllowed());
        }
        if (indexVersion != null && !indexVersion.isBlank()) {
            payload.put("indexVersion", indexVersion);
        }
        resolveModelPricing(request).ifPresent(model -> {
            payload.put("inputPricePerMillion", model.getInputPricePerMillion());
            payload.put("outputPricePerMillion", model.getOutputPricePerMillion());
        });
        return payload;
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
}
