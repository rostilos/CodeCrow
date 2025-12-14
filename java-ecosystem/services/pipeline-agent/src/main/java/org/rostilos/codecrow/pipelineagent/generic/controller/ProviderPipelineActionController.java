package org.rostilos.codecrow.pipelineagent.generic.controller;

import jakarta.servlet.http.HttpServletResponse;
import jakarta.validation.Valid;
import org.rostilos.codecrow.core.dto.project.ProjectDTO;
import org.rostilos.codecrow.core.model.job.Job;
import org.rostilos.codecrow.analysisengine.dto.request.processor.AnalysisProcessRequest;
import org.rostilos.codecrow.analysisengine.dto.request.processor.BranchProcessRequest;
import org.rostilos.codecrow.analysisengine.dto.request.processor.PrProcessRequest;
import org.rostilos.codecrow.pipelineagent.generic.processor.WebhookProcessor;
import org.rostilos.codecrow.pipelineagent.generic.service.PipelineJobService;
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

/**
 * Generic REST controller for handling VCS webhook events.
 * Works with any VCS provider through the generic WebhookProcessor.
 */
@RestController
@RequestMapping("/api/processing")
public class ProviderPipelineActionController {
    private static final Logger log = LoggerFactory.getLogger(ProviderPipelineActionController.class);
    private static final String EOF_MARKER = "__EOF__";

    private final WebhookProcessor webhookProcessor;
    private final PipelineJobService pipelineJobService;
    private final ObjectMapper objectMapper;

    public ProviderPipelineActionController(
            WebhookProcessor webhookProcessor, 
            PipelineJobService pipelineJobService,
            ObjectMapper objectMapper
    ) {
        this.webhookProcessor = webhookProcessor;
        this.pipelineJobService = pipelineJobService;
        this.objectMapper = objectMapper;
    }

    @PostMapping("/webhook/pr")
    public ResponseEntity<StreamingResponseBody> handlePrWebhook(
            @AuthenticationPrincipal ProjectDTO authenticationPrincipal,
            @Valid @RequestBody PrProcessRequest payload
    ) {
        Job job = pipelineJobService.createPipelinePrJob(payload);
        
        return processWebhookWithJob(
                authenticationPrincipal,
                payload,
                job,
                (consumer, jobRef) -> {
                    WebhookProcessor.EventConsumer dualConsumer = 
                            pipelineJobService.createDualConsumer(jobRef, consumer);
                    try {
                        return webhookProcessor.processWebhookWithConsumer(payload, dualConsumer);
                    } catch (org.rostilos.codecrow.analysisengine.exception.AnalysisLockedException e) {
                        log.warn("Analysis locked: {}", e.getMessage());
                        dualConsumer.accept(Map.of(
                                "type", "lock_wait",
                                "message", e.getMessage(),
                                "lockType", e.getLockType(),
                                "branchName", e.getBranchName(),
                                "projectId", e.getProjectId()
                        ));
                        return Map.of("status", "locked", "message", e.getMessage());
                    } catch (Exception e) {
                        log.error("Error in webhook processing", e);
                        dualConsumer.accept(Map.of(
                                "type", "error",
                                "message", "Processing failed: " + (e.getMessage() != null ? e.getMessage() : e.getClass().getSimpleName())
                        ));
                        return Map.of("status", "error", "message", e.getMessage());
                    }
                }
        );
    }

    @PostMapping(value = "/webhook/branch", consumes = {MediaType.APPLICATION_JSON_VALUE, MediaType.MULTIPART_FORM_DATA_VALUE})
    public ResponseEntity<StreamingResponseBody> handleBranchWebhook(
            @AuthenticationPrincipal ProjectDTO authenticationPrincipal,
            @RequestPart(value = "request", required = false) String requestJson,
            @RequestBody(required = false) String bodyJson,
            @RequestPart(value = "archive", required = false) MultipartFile archive
    ) {
        try {
            BranchProcessRequest payload;
            if (requestJson != null) {
                payload = objectMapper.readValue(requestJson, BranchProcessRequest.class);
            } else if (bodyJson != null) {
                payload = objectMapper.readValue(bodyJson, BranchProcessRequest.class);
            } else {
                throw new IllegalArgumentException("Request payload is required");
            }

            if (archive != null && !archive.isEmpty()) {
                payload.setArchive(archive.getBytes());
                log.info("Archive received: {} bytes", archive.getSize());
            }

            Job job = pipelineJobService.createPipelineBranchJob(payload);

            return processWebhookWithJob(
                    authenticationPrincipal,
                    payload,
                    job,
                    (consumer, jobRef) -> {
                        WebhookProcessor.EventConsumer dualConsumer = 
                                pipelineJobService.createDualConsumer(jobRef, consumer);
                        try {
                            return webhookProcessor.processWebhookWithConsumer(payload, dualConsumer);
                        } catch (org.rostilos.codecrow.analysisengine.exception.AnalysisLockedException e) {
                            log.warn("Analysis locked: {}", e.getMessage());
                            dualConsumer.accept(Map.of(
                                    "type", "lock_wait",
                                    "message", e.getMessage(),
                                    "lockType", e.getLockType(),
                                    "branchName", e.getBranchName(),
                                    "projectId", e.getProjectId()
                            ));
                            return Map.of("status", "locked", "message", e.getMessage());
                        } catch (Exception e) {
                            log.error("Error in webhook processing", e);
                            dualConsumer.accept(Map.of(
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

    @FunctionalInterface
    private interface JobAwareWebhookProcessor {
        Map<String, Object> process(WebhookProcessor.EventConsumer consumer, Job job);
    }

    private ResponseEntity<StreamingResponseBody> processWebhookWithJob(
            ProjectDTO authenticationPrincipal,
            AnalysisProcessRequest payload,
            Job job,
            JobAwareWebhookProcessor webhookProcessorFunc
    ) {
        try {
            validateAuthentication(authenticationPrincipal, payload);

            if (job != null) {
                pipelineJobService.getJobService().startJob(job);
            }

            LinkedBlockingQueue<String> queue = new LinkedBlockingQueue<>();
            WebhookProcessor.EventConsumer consumer = createEventConsumer(queue);

            CompletableFuture<Map<String, Object>> processingFuture = CompletableFuture.supplyAsync(() -> {
                try {
                    return webhookProcessorFunc.process(consumer, job);
                } catch (org.rostilos.codecrow.analysisengine.exception.AnalysisLockedException e) {
                    log.warn("Analysis locked: {}", e.getMessage());
                    return Map.of("status", "locked", "message", e.getMessage());
                } catch (Exception e) {
                    log.error("Error in webhook processing", e);
                    return Map.of("status", "error", "message", e.getMessage() != null ? e.getMessage() : e.getClass().getSimpleName());
                }
            });

            try {
                Map<String, Object> quickResult = processingFuture.get(100, java.util.concurrent.TimeUnit.MILLISECONDS);
                if ("locked".equals(quickResult.get("status"))) {
                    String lockMsg = objectMapper.writeValueAsString(
                            Map.of("type", "locked", "message", quickResult.get("message"))
                    );
                    queue.put(lockMsg);
                    enqueueEOF(queue);
                    if (job != null) {
                        pipelineJobService.getJobService().cancelJob(job);
                    }
                    return ResponseEntity.ok()
                            .contentType(MediaType.parseMediaType("application/x-ndjson"))
                            .body(createStreamingResponse(queue));
                }
            } catch (java.util.concurrent.TimeoutException e) {
                // Normal case - processing is taking time, continue with streaming
            }

            try {
                String initialMsg = objectMapper.writeValueAsString(
                        Map.of("type", "status", "state", "started", "message", "Analysis request received")
                );
                queue.put(initialMsg);
            } catch (Exception e) {
                log.warn("Failed to enqueue initial status", e);
            }

            processingFuture.whenComplete((result, throwable) -> {
                try {
                    if (throwable != null) {
                        enqueueError(queue, throwable);
                        pipelineJobService.failJob(job, throwable.getMessage());
                    } else {
                        Object status = result.get("status");
                        if ("error".equals(status)) {
                            log.warn("Webhook processing completed with error: {}", result.get("message"));
                            enqueueFinalResult(queue, result);
                            pipelineJobService.failJob(job, (String) result.get("message"));
                        } else if ("locked".equals(status)) {
                            log.info("Webhook processing locked: {}", result.get("message"));
                            enqueueFinalResult(queue, result);
                            if (job != null) {
                                pipelineJobService.getJobService().cancelJob(job);
                            }
                        } else {
                            enqueueFinalResult(queue);
                            pipelineJobService.completeJob(job, result);
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
            pipelineJobService.failJob(job, "Unauthorized: " + e.getMessage());
            return createErrorResponse(HttpServletResponse.SC_UNAUTHORIZED, "token_project_mismatch", null);
        } catch (IllegalArgumentException e) {
            log.error("Invalid webhook request: {}", e.getMessage());
            pipelineJobService.failJob(job, "Invalid request: " + e.getMessage());
            return createErrorResponse(HttpServletResponse.SC_BAD_REQUEST, "invalid_request", e.getMessage());
        } catch (Exception e) {
            log.error("Error processing webhook payload", e);
            pipelineJobService.failJob(job, "Processing failed: " + e.getMessage());
            return createErrorResponse(HttpServletResponse.SC_INTERNAL_SERVER_ERROR, "processing_failed", e.getMessage());
        }
    }

    private void validateAuthentication(ProjectDTO authenticationPrincipal, AnalysisProcessRequest payload) {
        if (!payload.getProjectId().equals(authenticationPrincipal.id())) {
            log.warn("Request body projectId {} does not match JWT projectId {}",
                    payload.getProjectId(), authenticationPrincipal.id());
            throw new UnauthorizedException("Project ID mismatch");
        }
    }

    private WebhookProcessor.EventConsumer createEventConsumer(LinkedBlockingQueue<String> queue) {
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

    private void enqueueEOF(LinkedBlockingQueue<String> queue) {
        try {
            queue.put(EOF_MARKER);
            log.debug("Enqueued EOF marker");
        } catch (InterruptedException ignored) {
            Thread.currentThread().interrupt();
        }
    }

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

    private static class UnauthorizedException extends RuntimeException {
        public UnauthorizedException(String message) {
            super(message);
        }
    }
}
