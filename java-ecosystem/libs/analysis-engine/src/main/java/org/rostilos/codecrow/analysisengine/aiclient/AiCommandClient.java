package org.rostilos.codecrow.analysisengine.aiclient;

import com.fasterxml.jackson.databind.ObjectMapper;
import org.rostilos.codecrow.queue.RedisQueueService;
import org.slf4j.Logger;
import org.slf4j.LoggerFactory;
import org.springframework.beans.factory.annotation.Autowired;
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
    static final String CONSUMER_HEARTBEAT_KEY =
            "codecrow:commands:consumer:heartbeat";
    private static final long DEFAULT_ADMISSION_TIMEOUT_MINUTES = 5L;
    private static final String ADMISSION_TIMEOUT_MINUTES_KEY =
            "COMMAND_QUEUE_ADMISSION_TIMEOUT_MINUTES";
    private final long commandTimeoutMillis;
    private final long admissionTimeoutMillis;

    @Autowired
    public AiCommandClient(RedisQueueService queueService, ObjectMapper objectMapper) {
        this(
                queueService,
                objectMapper,
                TimeUnit.MINUTES.toMillis(COMMAND_TIMEOUT_MINUTES),
                resolveAdmissionTimeoutMillis());
    }

    AiCommandClient(
            RedisQueueService queueService,
            ObjectMapper objectMapper,
            long commandTimeoutMillis,
            long admissionTimeoutMillis) {
        this.queueService = queueService;
        this.objectMapper = objectMapper;
        if (commandTimeoutMillis <= 0 || admissionTimeoutMillis <= 0) {
            throw new IllegalArgumentException("Command queue timeouts must be positive");
        }
        this.commandTimeoutMillis = commandTimeoutMillis;
        this.admissionTimeoutMillis = admissionTimeoutMillis;
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
                stringValue(finalResult, "summary", ""),
                stringValue(finalResult, "diagram", ""),
                stringValue(finalResult, "diagramType", "MERMAID"));
    }

    /**
     * Call the ask endpoint to answer a question.
     */
    public AskResult ask(AskRequest request, Consumer<Map<String, Object>> eventHandler)
            throws IOException {
        String jobId = UUID.randomUUID().toString();
        log.info("Sending ask request to Redis queue (Job ID: {})", jobId);

        Map<String, Object> finalResult = executeAsyncJob(jobId, "ask", request, eventHandler);

        return new AskResult(stringValue(finalResult, "answer", ""));
    }

    /**
     * Call the review endpoint to generate a code review.
     */
    public ReviewResult review(ReviewRequest request, Consumer<Map<String, Object>> eventHandler)
            throws IOException {
        String jobId = UUID.randomUUID().toString();
        log.info("Sending review request to Redis queue (Job ID: {})", jobId);

        Map<String, Object> finalResult = executeAsyncJob(jobId, "review", request, eventHandler);

        return new ReviewResult(stringValue(finalResult, "review", ""));
    }

    private static String stringValue(Map<String, Object> result, String key, String defaultValue) {
        Object value = result.get(key);
        return value == null ? defaultValue : String.valueOf(value);
    }

    @SuppressWarnings("unchecked")
    private Map<String, Object> executeAsyncJob(
            String jobId,
            String commandType,
            Object request,
            Consumer<Map<String, Object>> eventHandler) throws IOException {
        String eventQueueKey = "codecrow:analysis:events:" + jobId;
        String jobsQueueKey = "codecrow:queue:commands";
        String jsonPayload = null;

        try {
            if (!queueService.hasKey(CONSUMER_HEARTBEAT_KEY)) {
                throw new IOException(
                        "Inference Orchestrator is unavailable: no live command queue consumer");
            }
            Map<String, Object> jobPayload = Map.of(
                    "job_id", jobId,
                    "command_type", commandType,
                    "request", request);

            jsonPayload = objectMapper.writeValueAsString(jobPayload);
            queueService.leftPush(jobsQueueKey, jsonPayload);
            queueService.setExpiry(eventQueueKey, COMMAND_TIMEOUT_MINUTES + 1);

            long startTime = System.currentTimeMillis();
            boolean workerAcknowledged = false;

            while (true) {
                long now = System.currentTimeMillis();
                if (!workerAcknowledged) {
                    if (!queueService.hasKey(CONSUMER_HEARTBEAT_KEY)) {
                        throw new IOException(
                                "Inference Orchestrator became unavailable before "
                                        + "the command job was admitted");
                    }
                    if (now - startTime > admissionTimeoutMillis) {
                        throw new IOException(
                                "AI command was not admitted by Inference Orchestrator within "
                                        + TimeUnit.MILLISECONDS.toSeconds(admissionTimeoutMillis)
                                        + " seconds");
                    }
                }
                if (now - startTime > commandTimeoutMillis) {
                    throw new IOException(
                            "AI command timed out after " + commandTimeoutMillis
                                    + "ms for Job: " + jobId);
                }

                String eventJson = queueService.rightPop(eventQueueKey, 5);

                if (eventJson == null) {
                    continue; // Timeout on rightPop, continue to check overall timeout
                }
                workerAcknowledged = true;

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
            if (jsonPayload != null) {
                try {
                    queueService.removeFromList(jobsQueueKey, jsonPayload);
                } catch (Exception cleanupError) {
                    log.warn("Failed to remove command job {} from pending queue: {}",
                            jobId, cleanupError.getMessage());
                }
            }
            try {
                queueService.deleteKey(eventQueueKey);
            } catch (Exception ignored) {
            }
        }
    }

    private static long resolveAdmissionTimeoutMillis() {
        String configured = System.getProperty(ADMISSION_TIMEOUT_MINUTES_KEY);
        if (configured == null || configured.isBlank()) {
            configured = System.getenv(ADMISSION_TIMEOUT_MINUTES_KEY);
        }
        if (configured == null || configured.isBlank()) {
            return TimeUnit.MINUTES.toMillis(DEFAULT_ADMISSION_TIMEOUT_MINUTES);
        }
        try {
            long minutes = Long.parseLong(configured.trim());
            if (minutes <= 0) {
                throw new IllegalArgumentException(
                        ADMISSION_TIMEOUT_MINUTES_KEY + " must be a positive integer");
            }
            return TimeUnit.MINUTES.toMillis(minutes);
        } catch (NumberFormatException invalid) {
            throw new IllegalArgumentException(
                    ADMISSION_TIMEOUT_MINUTES_KEY + " must be a positive integer", invalid);
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
            String aiBaseUrl,
            Long pullRequestId,
            String sourceBranch,
            String targetBranch,
            String commitHash,
            String oAuthClient,
            String oAuthSecret,
            String accessToken,
            boolean supportsMermaid,
            Integer maxAllowedTokens,
            String vcsProvider,
            String vcsBaseUrl) {
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
            String aiBaseUrl,
            String question,
            Long pullRequestId,
            String commitHash,
            String oAuthClient,
            String oAuthSecret,
            String accessToken,
            Integer maxAllowedTokens,
            String vcsProvider,
            String vcsBaseUrl,
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
            String aiBaseUrl,
            Long pullRequestId,
            String sourceBranch,
            String targetBranch,
            String commitHash,
            String oAuthClient,
            String oAuthSecret,
            String accessToken,
            Integer maxAllowedTokens,
            String vcsProvider,
            String vcsBaseUrl) {
    }

    /**
     * Result from review endpoint.
     */
    public record ReviewResult(
            String review) {
    }
}
