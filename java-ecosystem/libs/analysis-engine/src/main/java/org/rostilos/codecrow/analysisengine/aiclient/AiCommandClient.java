package org.rostilos.codecrow.analysisengine.aiclient;

import com.fasterxml.jackson.databind.ObjectMapper;
import org.rostilos.codecrow.queue.RedisQueueService;
import org.slf4j.Logger;
import org.slf4j.LoggerFactory;
import org.springframework.stereotype.Service;

import java.io.IOException;
import java.util.Map;
import java.util.UUID;
import java.util.concurrent.TimeUnit;
import java.util.function.Consumer;

/**
 * Client for communicating with the AI service for CodeCrow commands
 * (summarize, ask).
 * Uses an async queue architecture backed by Redis via codecrow-queue.
 */
@Service
public class AiCommandClient {
    private static final Logger log = LoggerFactory.getLogger(AiCommandClient.class);

    private final RedisQueueService queueService;
    private final ObjectMapper objectMapper;
    private static final int COMMAND_TIMEOUT_MINUTES = 30;

    public AiCommandClient(RedisQueueService queueService, ObjectMapper objectMapper) {
        this.queueService = queueService;
        this.objectMapper = objectMapper;
    }

    /**
     * Call the summarize endpoint to generate a PR summary.
     */
    public SummarizeResult summarize(SummarizeRequest request, Consumer<Map<String, Object>> eventHandler)
            throws IOException {
        String jobId = UUID.randomUUID().toString();
        log.info("Sending summarize request to Redis queue (Job ID: {})", jobId);

        Map<String, Object> finalResult = executeAsyncJob(jobId, "summarize", request, eventHandler);

        return new SummarizeResult(
                (String) finalResult.getOrDefault("summary", ""),
                (String) finalResult.getOrDefault("diagram", ""),
                (String) finalResult.getOrDefault("diagramType", "MERMAID"));
    }

    /**
     * Call the ask endpoint to answer a question.
     */
    public AskResult ask(AskRequest request, Consumer<Map<String, Object>> eventHandler)
            throws IOException {
        String jobId = UUID.randomUUID().toString();
        log.info("Sending ask request to Redis queue (Job ID: {})", jobId);

        Map<String, Object> finalResult = executeAsyncJob(jobId, "ask", request, eventHandler);

        return new AskResult((String) finalResult.getOrDefault("answer", ""));
    }

    /**
     * Call the review endpoint to generate a code review.
     */
    public ReviewResult review(ReviewRequest request, Consumer<Map<String, Object>> eventHandler)
            throws IOException {
        String jobId = UUID.randomUUID().toString();
        log.info("Sending review request to Redis queue (Job ID: {})", jobId);

        Map<String, Object> finalResult = executeAsyncJob(jobId, "review", request, eventHandler);

        return new ReviewResult((String) finalResult.getOrDefault("review", ""));
    }

    @SuppressWarnings("unchecked")
    private Map<String, Object> executeAsyncJob(
            String jobId,
            String commandType,
            Object request,
            Consumer<Map<String, Object>> eventHandler) throws IOException {
        String eventQueueKey = "codecrow:analysis:events:" + jobId;
        String jobsQueueKey = "codecrow:queue:commands";

        try {
            Map<String, Object> jobPayload = Map.of(
                    "job_id", jobId,
                    "command_type", commandType,
                    "request", request);

            String jsonPayload = objectMapper.writeValueAsString(jobPayload);
            queueService.leftPush(jobsQueueKey, jsonPayload);
            queueService.setExpiry(eventQueueKey, COMMAND_TIMEOUT_MINUTES + 1);

            long startTime = System.currentTimeMillis();
            long timeoutMillis = TimeUnit.MINUTES.toMillis(COMMAND_TIMEOUT_MINUTES);

            while (true) {
                if (System.currentTimeMillis() - startTime > timeoutMillis) {
                    throw new IOException(
                            "AI command timed out after " + COMMAND_TIMEOUT_MINUTES + " minutes for Job: " + jobId);
                }

                String eventJson = queueService.rightPop(eventQueueKey, 5);

                if (eventJson == null) {
                    continue; // Timeout on rightPop, continue to check overall timeout
                }

                try {
                    Map<String, Object> event = objectMapper.readValue(eventJson, Map.class);

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
                        if (res instanceof Map) {
                            return (Map<String, Object>) res;
                        } else if (res != null) {
                            return Map.of("result", res);
                        } else {
                            throw new IOException("AI service returned final event without a valid result payload");
                        }
                    }
                } catch (IOException ex) {
                    throw ex;
                } catch (Exception ex) {
                    log.warn("Failed to parse Redis event JSON: {}", ex.getMessage(), ex);
                }
            }

        } catch (Exception e) {
            log.error("Failed to communicate with AI async queue", e);
            throw new IOException("AI queue communication failed: " + e.getMessage(), e);
        } finally {
            try {
                queueService.deleteKey(eventQueueKey);
            } catch (Exception ignored) {
            }
        }
    }

    /**
     * Request object for summarize endpoint.
     */
    public record SummarizeRequest(
            Long projectId,
            String projectVcsWorkspace,
            String projectVcsRepoSlug,
            String projectWorkspace,
            String projectNamespace,
            String aiProvider,
            String aiModel,
            String aiApiKey,
            Long pullRequestId,
            String sourceBranch,
            String targetBranch,
            String commitHash,
            String oAuthClient,
            String oAuthSecret,
            String accessToken,
            boolean supportsMermaid,
            Integer maxAllowedTokens,
            String vcsProvider) {
    }

    /**
     * Request object for ask endpoint.
     */
    public record AskRequest(
            Long projectId,
            String projectVcsWorkspace,
            String projectVcsRepoSlug,
            String projectWorkspace,
            String projectNamespace,
            String aiProvider,
            String aiModel,
            String aiApiKey,
            String question,
            Long pullRequestId,
            String commitHash,
            String oAuthClient,
            String oAuthSecret,
            String accessToken,
            Integer maxAllowedTokens,
            String vcsProvider,
            String analysisContext,
            java.util.List<String> issueReferences) {
    }

    /**
     * Result from summarize endpoint.
     */
    public record SummarizeResult(
            String summary,
            String diagram,
            String diagramType) {
    }

    /**
     * Result from ask endpoint.
     */
    public record AskResult(
            String answer) {
    }

    /**
     * Request object for review endpoint.
     */
    public record ReviewRequest(
            Long projectId,
            String projectVcsWorkspace,
            String projectVcsRepoSlug,
            String projectWorkspace,
            String projectNamespace,
            String aiProvider,
            String aiModel,
            String aiApiKey,
            Long pullRequestId,
            String sourceBranch,
            String targetBranch,
            String commitHash,
            String oAuthClient,
            String oAuthSecret,
            String accessToken,
            Integer maxAllowedTokens,
            String vcsProvider) {
    }

    /**
     * Result from review endpoint.
     */
    public record ReviewResult(
            String review) {
    }
}
