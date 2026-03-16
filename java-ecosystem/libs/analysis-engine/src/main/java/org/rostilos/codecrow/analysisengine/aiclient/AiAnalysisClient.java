package org.rostilos.codecrow.analysisengine.aiclient;

import com.fasterxml.jackson.databind.ObjectMapper;
import org.rostilos.codecrow.analysisengine.dto.request.ai.AiAnalysisRequest;
import org.rostilos.codecrow.analysisengine.dto.request.ai.AiAnalysisRequestImpl;
import org.slf4j.Logger;
import org.slf4j.LoggerFactory;
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

    private static final int REVIEW_TIMEOUT_MINUTES = 30;

    public AiAnalysisClient(
            @Qualifier("aiRestTemplate") RestTemplate restTemplate,
            RedisQueueService queueService,
            ObjectMapper objectMapper) {
        // restTemplate kept in constructor for backward compatibility but no longer
        // used
        this.queueService = queueService;
        this.objectMapper = objectMapper;
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

        try {
            log.info("Sending async analysis request to Redis queue (Job ID: {})", jobId);

            // Wrap the request with the jobId
            Map<String, Object> jobPayload = Map.of(
                    "job_id", jobId,
                    "request", buildSerializableRequestPayload(request));

            String jsonPayload = objectMapper.writeValueAsString(jobPayload);

            // Push the job to the Redis queue
            queueService.leftPush(jobsQueueKey, jsonPayload);

            // Set an expiration on the event queue to prevent orphaned keys if everything
            // crashes
            queueService.setExpiry(eventQueueKey, REVIEW_TIMEOUT_MINUTES + 1);

            long startTime = System.currentTimeMillis();
            long timeoutMillis = TimeUnit.MINUTES.toMillis(REVIEW_TIMEOUT_MINUTES);

            // Poll the event queue for progress or final result
            while (true) {
                if (System.currentTimeMillis() - startTime > timeoutMillis) {
                    throw new IOException(
                            "AI Analysis timed out after " + REVIEW_TIMEOUT_MINUTES + " minutes for Job: " + jobId);
                }

                String eventJson = queueService.rightPop(eventQueueKey, 5);

                if (eventJson == null) {
                    continue; // Timeout on BLPOP, continue to check overall timeout
                }

                try {
                    Map<String, Object> event = objectMapper.readValue(eventJson, Map.class);

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
                    log.warn("Failed to parse Redis event JSON: {}", ex.getMessage(), ex);
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
        payload.put("reconciliationFileContents", request.getReconciliationFileContents());
        if (request instanceof AiAnalysisRequestImpl impl) {
            payload.put("enrichmentData", impl.getEnrichmentData());
            payload.put("projectRules", impl.getProjectRules());
        }
        return payload;
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
