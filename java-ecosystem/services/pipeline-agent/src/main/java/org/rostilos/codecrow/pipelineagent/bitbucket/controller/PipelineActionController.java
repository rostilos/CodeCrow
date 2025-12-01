package org.rostilos.codecrow.pipelineagent.bitbucket.controller;

import jakarta.servlet.http.HttpServletResponse;
import jakarta.validation.Valid;
import org.rostilos.codecrow.core.dto.project.ProjectDTO;
import org.rostilos.codecrow.pipelineagent.generic.dto.request.processor.AnalysisProcessRequest;
import org.rostilos.codecrow.pipelineagent.generic.dto.request.processor.BranchProcessRequest;
import org.rostilos.codecrow.pipelineagent.generic.dto.request.processor.PrProcessRequest;
import org.rostilos.codecrow.pipelineagent.bitbucket.processor.BitbucketWebhookProcessor;
import org.slf4j.Logger;
import org.slf4j.LoggerFactory;
import org.springframework.http.ResponseEntity;
import org.springframework.http.MediaType;
import org.springframework.security.core.annotation.AuthenticationPrincipal;
import org.springframework.web.bind.annotation.PostMapping;
import org.springframework.web.bind.annotation.RequestBody;
import org.springframework.web.bind.annotation.RequestMapping;
import org.springframework.web.bind.annotation.RequestPart;
import org.springframework.web.bind.annotation.RestController;
import org.springframework.web.multipart.MultipartFile;
import org.springframework.web.servlet.mvc.method.annotation.StreamingResponseBody;
import com.fasterxml.jackson.databind.ObjectMapper;

import java.io.IOException;
import java.util.concurrent.CompletableFuture;
import java.util.concurrent.LinkedBlockingQueue;
import java.util.Map;
import java.nio.charset.StandardCharsets;
import java.io.OutputStream;
import java.io.PrintWriter;
import java.util.function.Function;

/**
 * TODO: move to generic, adapt for multi-vcs support
 * REST controller for handling Bitbucket webhook events.
 * Performs authentication via JWT and delegates processing to service layer.
 */
@RestController
@RequestMapping("/api/processing")
public class PipelineActionController {
    private static final Logger log = LoggerFactory.getLogger(PipelineActionController.class);
    private static final String EOF_MARKER = "__EOF__";

    private final BitbucketWebhookProcessor webhookProcessor;
    private final ObjectMapper objectMapper;

    public PipelineActionController(BitbucketWebhookProcessor webhookProcessor, ObjectMapper objectMapper) {
        this.webhookProcessor = webhookProcessor;
        this.objectMapper = objectMapper;
    }

    @PostMapping("/bitbucket/webhook/pr")
    public ResponseEntity<StreamingResponseBody> handleBitbucketPrWebhook(
            @AuthenticationPrincipal ProjectDTO authenticationPrincipal,
            @Valid @RequestBody PrProcessRequest payload
    ) {
        return processWebhook(
                authenticationPrincipal,
                payload,
                consumer -> {
                    try {
                        return webhookProcessor.processWebhookWithConsumer(payload, consumer);
                    } catch (org.rostilos.codecrow.pipelineagent.generic.exception.AnalysisLockedException e) {
                        log.warn("Analysis locked: {}", e.getMessage());
                        consumer.accept(Map.of(
                                "type", "lock_wait",
                                "message", e.getMessage(),
                                "lockType", e.getLockType(),
                                "branchName", e.getBranchName(),
                                "projectId", e.getProjectId()
                        ));
                        return Map.of("status", "locked", "message", e.getMessage());
                    } catch (Exception e) {
                        log.error("Error in webhook processing", e);
                        consumer.accept(Map.of(
                                "type", "error",
                                "message", "Processing failed: " + (e.getMessage() != null ? e.getMessage() : e.getClass().getSimpleName())
                        ));
                        return Map.of("status", "error", "message", e.getMessage());
                    }
                }
        );
    }

    @PostMapping(value = "/bitbucket/webhook/branch", consumes = {MediaType.APPLICATION_JSON_VALUE, MediaType.MULTIPART_FORM_DATA_VALUE})
    public ResponseEntity<StreamingResponseBody> handleBitbucketBranchWebhook(
            @AuthenticationPrincipal ProjectDTO authenticationPrincipal,
            @RequestPart(value = "request", required = false) String requestJson,
            @RequestBody(required = false) String bodyJson,
            @RequestPart(value = "archive", required = false) MultipartFile archive
    ) {
        try {
            // Parse request from either multipart part or JSON body
            BranchProcessRequest payload;
            if (requestJson != null) {
                payload = objectMapper.readValue(requestJson, BranchProcessRequest.class);
            } else if (bodyJson != null) {
                payload = objectMapper.readValue(bodyJson, BranchProcessRequest.class);
            } else {
                throw new IllegalArgumentException("Request payload is required");
            }

            // Attach archive if provided
            if (archive != null && !archive.isEmpty()) {
                payload.setArchive(archive.getBytes());
                log.info("Archive received: {} bytes", archive.getSize());
            }

            return processWebhook(
                    authenticationPrincipal,
                    payload,
                    consumer -> {
                        try {
                            return webhookProcessor.processWebhookWithConsumer(payload, consumer);
                        } catch (org.rostilos.codecrow.pipelineagent.generic.exception.AnalysisLockedException e) {
                            log.warn("Analysis locked: {}", e.getMessage());
                            consumer.accept(Map.of(
                                    "type", "lock_wait",
                                    "message", e.getMessage(),
                                    "lockType", e.getLockType(),
                                    "branchName", e.getBranchName(),
                                    "projectId", e.getProjectId()
                            ));
                            return Map.of("status", "locked", "message", e.getMessage());
                        } catch (Exception e) {
                            log.error("Error in webhook processing", e);
                            consumer.accept(Map.of(
                                    "type", "error",
                                    "message", "Processing failed: " + (e.getMessage() != null ? e.getMessage() : e.getClass().getSimpleName())
                            ));
                            return Map.of("status", "error", "message", e.getMessage());
                        }
                    }
            );
        } catch (IOException e) {
            log.error("Failed to parse request or read archive", e);
            return createErrorResponse(HttpServletResponse.SC_BAD_REQUEST, "invalid_request", e.getMessage());
        }
    }

    /**
     * Common webhook processing logic that handles authentication, streaming, and error handling.
     *
     * @param authenticationPrincipal JWT authenticated project
     * @param payload Webhook request payload
     * @param webhookProcessor Function that takes an EventConsumer and processes the webhook
     */
    private ResponseEntity<StreamingResponseBody> processWebhook(
            ProjectDTO authenticationPrincipal,
            AnalysisProcessRequest payload,
            Function<BitbucketWebhookProcessor.EventConsumer, Map<String, Object>> webhookProcessor
    ) {
        try {
            validateAuthentication(authenticationPrincipal, payload);

            LinkedBlockingQueue<String> queue = new LinkedBlockingQueue<>();
            BitbucketWebhookProcessor.EventConsumer consumer = createEventConsumer(queue);

            // Start background processing first to check for locks early
            CompletableFuture<Map<String, Object>> processingFuture = CompletableFuture.supplyAsync(() -> {
                try {
                    return webhookProcessor.apply(consumer);
                } catch (org.rostilos.codecrow.pipelineagent.generic.exception.AnalysisLockedException e) {
                    log.warn("Analysis locked: {}", e.getMessage());
                    return Map.of("status", "locked", "message", e.getMessage());
                } catch (Exception e) {
                    log.error("Error in webhook processing", e);
                    return Map.of("status", "error", "message", e.getMessage() != null ? e.getMessage() : e.getClass().getSimpleName());
                }
            });

            // Wait briefly to see if we get an immediate lock error
            try {
                Map<String, Object> quickResult = processingFuture.get(100, java.util.concurrent.TimeUnit.MILLISECONDS);
                // Got result immediately (likely a lock error)
                if ("locked".equals(quickResult.get("status"))) {
                    String lockMsg = objectMapper.writeValueAsString(
                            Map.of("type", "locked", "message", quickResult.get("message"))
                    );
                    queue.put(lockMsg);
                    enqueueEOF(queue);
                    return ResponseEntity.ok()
                            .contentType(MediaType.parseMediaType("application/x-ndjson"))
                            .body(createStreamingResponse(queue));
                }
            } catch (java.util.concurrent.TimeoutException e) {
                // Normal case - processing is taking time, continue with streaming
            }

            // Send initial status message after confirming no immediate lock
            try {
                String initialMsg = objectMapper.writeValueAsString(
                        Map.of("type", "status", "state", "started", "message", "Analysis request received")
                );
                queue.put(initialMsg);
            } catch (Exception e) {
                log.warn("Failed to enqueue initial status", e);
            }

            // Continue monitoring the processing future
            processingFuture.whenComplete((result, throwable) -> {
                try {
                    if (throwable != null) {
                        enqueueError(queue, throwable);
                    } else {
                        Object status = result.get("status");
                        if ("error".equals(status)) {
                            log.warn("Webhook processing completed with error: {}", result.get("message"));
                            enqueueFinalResult(queue, result);
                        } else if ("locked".equals(status)) {
                            log.info("Webhook processing locked: {}", result.get("message"));
                            enqueueFinalResult(queue, result);
                        } else {
                            enqueueFinalResult(queue);
                        }
                    }
                } catch (Exception e) {
                    log.error("Error in completion handler", e);
                } finally {
                    try {
                        enqueueEOF(queue);
                    } catch (Exception e) {
                        log.error("Failed to enqueue EOF marker", e);
                    }
                }
            });

            return ResponseEntity.ok()
                    .contentType(MediaType.parseMediaType("application/x-ndjson"))
                    .body(createStreamingResponse(queue));

        } catch (UnauthorizedException e) {
            log.warn("Unauthorized webhook request: {}", e.getMessage());
            return createErrorResponse(HttpServletResponse.SC_UNAUTHORIZED, "token_project_mismatch", null);
        } catch (IllegalArgumentException e) {
            log.error("Invalid webhook request: {}", e.getMessage());
            return createErrorResponse(HttpServletResponse.SC_BAD_REQUEST, "invalid_request", e.getMessage());
        } catch (Exception e) {
            log.error("Error processing webhook payload", e);
            return createErrorResponse(HttpServletResponse.SC_INTERNAL_SERVER_ERROR, "processing_failed", e.getMessage());
        }
    }

    /**
     * Validates that the JWT project ID matches the payload project ID.
     */
    private void validateAuthentication(ProjectDTO authenticationPrincipal, AnalysisProcessRequest payload) {
        if (!payload.getProjectId().equals(authenticationPrincipal.id())) {
            log.warn("Request body projectId {} does not match JWT projectId {}",
                    payload.getProjectId(), authenticationPrincipal.id());
            throw new UnauthorizedException("Project ID mismatch");
        }
    }

    /**
     * Creates an event consumer that serializes events to JSON and adds them to the queue.
     */
    private BitbucketWebhookProcessor.EventConsumer createEventConsumer(LinkedBlockingQueue<String> queue) {
        return event -> {
            try {
                log.debug("Event consumer received event: type={}", event.get("type"));
                String json = objectMapper.writeValueAsString(event);
                queue.put(json);
                log.debug("Event enqueued successfully, queue size: {}", queue.size());
            } catch (InterruptedException e) {
                Thread.currentThread().interrupt();
                log.warn("Interrupted while enqueuing event", e);
            } catch (Exception ex) {
                log.warn("Failed to enqueue event: {}", ex.getMessage(), ex);
            }
        };
    }

    /**
     * Starts background processing of the webhook and feeds results into the queue.
     *
     * @param queue Queue for NDJSON events
     * @param consumer Event consumer to pass to the webhook processor
     * @param webhookProcessor Function that processes the webhook with the given consumer
     */
    private void startBackgroundProcessing(
            LinkedBlockingQueue<String> queue,
            BitbucketWebhookProcessor.EventConsumer consumer,
            Function<BitbucketWebhookProcessor.EventConsumer, Map<String, Object>> webhookProcessor
    ) {
        CompletableFuture.runAsync(() -> {
            try {
                log.debug("Starting webhook processing in background");
                Map<String, Object> result = webhookProcessor.apply(consumer);

                Object status = result.get("status");
                if ("error".equals(status)) {
                    log.warn("Webhook processing completed with error: {}", result.get("message"));
                    enqueueFinalResult(queue, result);
                } else if ("locked".equals(status)) {
                    log.info("Webhook processing locked: {}", result.get("message"));
                    enqueueFinalResult(queue, result);
                } else {
                    enqueueFinalResult(queue);
                }
            } catch (Exception e) {
                log.error("Unexpected error in background processing", e);
                enqueueError(queue, e);
            } finally {
                try {
                    enqueueEOF(queue);
                } catch (Exception e) {
                    log.error("Failed to enqueue EOF marker", e);
                }
            }
        }).exceptionally(throwable -> {
            log.error("CompletableFuture exceptionally handler triggered", throwable);
            try {
                enqueueError(queue, throwable);
                enqueueEOF(queue);
            } catch (Exception e) {
                log.error("Failed to enqueue error in exceptionally handler", e);
            }
            return null;
        });
    }

    /**
     * Creates a streaming response body that drains the queue and writes NDJSON lines.
     */
    private StreamingResponseBody createStreamingResponse(LinkedBlockingQueue<String> queue) {
        return (OutputStream outputStream) -> {
            PrintWriter writer = new PrintWriter(outputStream, true, StandardCharsets.UTF_8);
            try {
                String line;
                while ((line = queue.take()) != null) {
                    if (EOF_MARKER.equals(line)) {
                        break;
                    }
                    writer.println(line);
                    writer.flush();
                    outputStream.flush();
                }
                log.debug("Streaming completed");
            } catch (InterruptedException e) {
                Thread.currentThread().interrupt();
                log.warn("Streaming interrupted", e);
            } finally {
                writer.close();
            }
        };
    }

    /**
     * Enqueues the final result as a JSON event.
     */
    private void enqueueFinalResult(LinkedBlockingQueue<String> queue) {
        try {
            String finalJson = objectMapper.writeValueAsString(
                    Map.of("type", "final", "result", "Report in the process of being posted on the VCS platform")
            );
            queue.put(finalJson);
            log.debug("Enqueued final result");
        } catch (InterruptedException e) {
            Thread.currentThread().interrupt();
            log.warn("Interrupted while enqueuing final result", e);
        } catch (Exception ex) {
            log.warn("Failed to enqueue final event: {}", ex.getMessage());
        }
    }

    /**
     * Enqueues the final result with custom data as a JSON event.
     */
    private void enqueueFinalResult(LinkedBlockingQueue<String> queue, Map<String, Object> result) {
        try {
            String finalJson = objectMapper.writeValueAsString(
                    Map.of("type", "final", "result", result)
            );
            queue.put(finalJson);
            log.debug("Enqueued final result with custom data: {}", result);
        } catch (InterruptedException e) {
            Thread.currentThread().interrupt();
            log.warn("Interrupted while enqueuing final result", e);
        } catch (Exception ex) {
            log.warn("Failed to enqueue final event: {}", ex.getMessage());
        }
    }

    /**
     * Enqueues an error event.
     */
    private void enqueueError(LinkedBlockingQueue<String> queue, Throwable e) {
        try {
            String err = objectMapper.writeValueAsString(
                    Map.of("type", "error", "message", e.getMessage() != null ? e.getMessage() : e.getClass().getSimpleName())
            );
            queue.put(err);
        } catch (InterruptedException ie) {
            Thread.currentThread().interrupt();
            log.error("Interrupted while enqueuing error event", ie);
        } catch (Exception ignored) {
            log.error("Failed to enqueue error event", ignored);
        }
    }

    /**
     * Enqueues the EOF marker to signal stream completion.
     */
    private void enqueueEOF(LinkedBlockingQueue<String> queue) {
        try {
            queue.put(EOF_MARKER);
            log.debug("Enqueued EOF marker");
        } catch (InterruptedException ignored) {
            Thread.currentThread().interrupt();
        }
    }

    /**
     * Creates an error response with a JSON body.
     */
    private ResponseEntity<StreamingResponseBody> createErrorResponse(int status, String error, String message) {
        return ResponseEntity.status(status)
                .body(outputStream -> {
                    PrintWriter w = new PrintWriter(outputStream, true, StandardCharsets.UTF_8);
                    try {
                        Map<String, String> errorBody = message != null
                                ? Map.of("error", error, "message", message)
                                : Map.of("error", error);
                        w.println(objectMapper.writeValueAsString(errorBody));
                    } catch (Exception e) {
                        w.println("{\"error\":\"" + error + "\"}");
                    }
                });
    }

    /**
     * Custom exception for authentication failures.
     */
    private static class UnauthorizedException extends RuntimeException {
        public UnauthorizedException(String message) {
            super(message);
        }
    }
}