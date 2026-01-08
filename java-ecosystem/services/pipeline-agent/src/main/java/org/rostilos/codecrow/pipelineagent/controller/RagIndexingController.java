package org.rostilos.codecrow.pipelineagent.controller;

import com.fasterxml.jackson.databind.ObjectMapper;
import org.rostilos.codecrow.core.dto.project.ProjectDTO;
import org.rostilos.codecrow.analysisengine.service.AnalysisLockService;
import org.rostilos.codecrow.ragengine.service.RagIndexTrackingService;
import org.rostilos.codecrow.ragengine.service.VcsRagIndexingService;
import org.slf4j.Logger;
import org.slf4j.LoggerFactory;
import org.springframework.http.MediaType;
import org.springframework.http.ResponseEntity;
import org.springframework.security.core.annotation.AuthenticationPrincipal;
import org.springframework.web.bind.annotation.*;
import org.springframework.web.servlet.mvc.method.annotation.StreamingResponseBody;

import jakarta.validation.Valid;
import java.io.PrintWriter;
import java.nio.charset.StandardCharsets;
import java.util.Map;
import java.util.concurrent.CompletableFuture;
import java.util.concurrent.LinkedBlockingQueue;
import java.util.function.Consumer;

/**
 * REST controller for RAG indexing operations.
 * Handles initial full-repository indexing and manual re-indexing triggers.
 */
@RestController
@RequestMapping("/api/rag")
public class RagIndexingController {
    private static final Logger log = LoggerFactory.getLogger(RagIndexingController.class);
    private static final String EOF_MARKER = "__EOF__";

    private final VcsRagIndexingService vcsRagIndexingService;
    private final RagIndexTrackingService ragIndexTrackingService;
    private final AnalysisLockService analysisLockService;
    private final ObjectMapper objectMapper;

    public RagIndexingController(
            VcsRagIndexingService vcsRagIndexingService,
            RagIndexTrackingService ragIndexTrackingService,
            AnalysisLockService analysisLockService,
            ObjectMapper objectMapper
    ) {
        this.vcsRagIndexingService = vcsRagIndexingService;
        this.ragIndexTrackingService = ragIndexTrackingService;
        this.analysisLockService = analysisLockService;
        this.objectMapper = objectMapper;
    }

    /**
     * Trigger RAG indexing for a project.
     * Downloads the repository from VCS and indexes it in the RAG pipeline.
     */
    @PostMapping(value = "/index", produces = MediaType.TEXT_EVENT_STREAM_VALUE)
    public ResponseEntity<StreamingResponseBody> triggerIndexing(
            @AuthenticationPrincipal ProjectDTO authProject,
            @Valid @RequestBody RagIndexRequest request
    ) {
        log.info("RAG indexing triggered for project: {}", authProject.namespace());

        StreamingResponseBody responseBody = outputStream -> {
            PrintWriter writer = new PrintWriter(outputStream, true, StandardCharsets.UTF_8);
            LinkedBlockingQueue<Map<String, Object>> messageQueue = new LinkedBlockingQueue<>();
            
            Consumer<Map<String, Object>> messageConsumer = msg -> {
                try {
                    messageQueue.put(msg);
                } catch (InterruptedException e) {
                    Thread.currentThread().interrupt();
                }
            };

            CompletableFuture<Map<String, Object>> indexingFuture = CompletableFuture.supplyAsync(() -> {
                try {
                    return vcsRagIndexingService.indexProjectFromVcs(
                            authProject,
                            request.branch(),
                            messageConsumer
                    );
                } catch (Exception e) {
                    log.error("RAG indexing failed", e);
                    return Map.of(
                            "status", "error",
                            "message", e.getMessage() != null ? e.getMessage() : e.getClass().getSimpleName()
                    );
                } finally {
                    messageConsumer.accept(Map.of("_eof", true));
                }
            });

            // Stream messages to client
            try {
                while (true) {
                    Map<String, Object> msg = messageQueue.take();
                    if (msg.containsKey("_eof")) {
                        break;
                    }
                    
                    String json = objectMapper.writeValueAsString(msg);
                    writer.println("data: " + json);
                    writer.println();
                    writer.flush();
                }

                // Send final result
                Map<String, Object> result = indexingFuture.get();
                String resultJson = objectMapper.writeValueAsString(result);
                writer.println("data: " + resultJson);
                writer.println();
                writer.println("data: " + EOF_MARKER);
                writer.flush();
                
            } catch (Exception e) {
                log.error("Error streaming RAG indexing progress", e);
                try {
                    String errorJson = objectMapper.writeValueAsString(Map.of(
                            "type", "error",
                            "message", "Streaming error: " + e.getMessage()
                    ));
                    writer.println("data: " + errorJson);
                    writer.println();
                    writer.println("data: " + EOF_MARKER);
                    writer.flush();
                } catch (Exception ignored) {}
            }
        };

        return ResponseEntity.ok()
                .contentType(MediaType.TEXT_EVENT_STREAM)
                .body(responseBody);
    }

    /**
     * Check if RAG indexing can be started for a project.
     */
    @GetMapping("/can-index/{projectId}")
    public ResponseEntity<?> canStartIndexing(@PathVariable Long projectId) {
        // TODO: This would need project lookup - simplified for now
        return ResponseEntity.ok(Map.of(
                "canIndex", true,
                "message", "Use the project-specific endpoint for accurate status"
        ));
    }

    public record RagIndexRequest(
            String branch  // Optional: branch to index. If null, uses project's configured RAG branch or default
    ) {}
}
