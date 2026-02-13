package org.rostilos.codecrow.webserver.project.service;

import com.fasterxml.jackson.databind.ObjectMapper;
import okhttp3.*;
import org.rostilos.codecrow.core.model.project.Project;
import org.rostilos.codecrow.core.persistence.repository.project.ProjectRepository;
import org.rostilos.codecrow.security.jwt.utils.JwtUtils;
import org.rostilos.codecrow.webserver.admin.service.ISiteSettingsProvider;
import org.slf4j.Logger;
import org.slf4j.LoggerFactory;
import org.springframework.scheduling.annotation.Async;
import org.springframework.stereotype.Service;
import org.springframework.web.servlet.mvc.method.annotation.SseEmitter;

import java.io.BufferedReader;
import java.io.IOException;
import java.io.InputStreamReader;
import java.time.Instant;
import java.time.temporal.ChronoUnit;
import java.util.Date;
import java.util.Map;
import java.util.NoSuchElementException;
import java.util.concurrent.ConcurrentHashMap;
import java.util.concurrent.TimeUnit;

/**
 * Service for triggering RAG indexing securely from the web-server.
 * This service:
 * - Generates a short-lived project JWT for pipeline-agent authentication
 * - Proxies the SSE stream from pipeline-agent back to the frontend
 * - Implements in-memory rate limiting per project
 */
@Service
public class RagIndexingTriggerService {
    private static final Logger log = LoggerFactory.getLogger(RagIndexingTriggerService.class);

    private static final String EOF_MARKER = "__EOF__";
    private static final long SHORT_LIVED_TOKEN_DURATION_MINUTES = 30;
    private static final long MIN_TRIGGER_INTERVAL_SECONDS = 60; // Prevent spam

    private final ProjectRepository projectRepository;
    private final JwtUtils jwtUtils;
    private final ObjectMapper objectMapper;
    private final OkHttpClient httpClient;

    // Simple in-memory rate limiting: projectId -> last trigger timestamp
    private final ConcurrentHashMap<Long, Instant> lastTriggerTimes = new ConcurrentHashMap<>();
    private final ISiteSettingsProvider siteSettingsProvider;

    @org.springframework.beans.factory.annotation.Value("${codecrow.rag.api.enabled:true}")
    private boolean ragApiEnabled;

    public RagIndexingTriggerService(
            ProjectRepository projectRepository,
            JwtUtils jwtUtils,
            ObjectMapper objectMapper,
            ISiteSettingsProvider siteSettingsProvider
    ) {
        this.projectRepository = projectRepository;
        this.jwtUtils = jwtUtils;
        this.objectMapper = objectMapper;
        this.siteSettingsProvider = siteSettingsProvider;

        // Configure OkHttpClient with appropriate timeouts for long-running SSE
        this.httpClient = new OkHttpClient.Builder()
                .connectTimeout(30, TimeUnit.SECONDS)
                .readTimeout(30, TimeUnit.MINUTES) // Long timeout for indexing
                .writeTimeout(30, TimeUnit.SECONDS)
                .build();
    }

    /**
     * Check if RAG indexing can be triggered for this project.
     * Validates rate limiting and configuration.
     */
    public RagTriggerValidationResult validateTrigger(Long projectId, Long userId) {
        if (!ragApiEnabled) {
            return new RagTriggerValidationResult(false, "RAG API is disabled on this server");
        }

        String pipelineUrl = getPipelineAgentBaseUrl();
        if (pipelineUrl == null || pipelineUrl.isBlank()) {
            log.warn("Pipeline agent URL is not configured");
            return new RagTriggerValidationResult(false, "Pipeline agent is not configured");
        }

        // Check rate limit
        Instant lastTrigger = lastTriggerTimes.get(projectId);
        if (lastTrigger != null) {
            long secondsSinceLastTrigger = ChronoUnit.SECONDS.between(lastTrigger, Instant.now());
            if (secondsSinceLastTrigger < MIN_TRIGGER_INTERVAL_SECONDS) {
                long waitSeconds = MIN_TRIGGER_INTERVAL_SECONDS - secondsSinceLastTrigger;
                return new RagTriggerValidationResult(
                        false,
                        String.format("Please wait %d seconds before triggering indexing again", waitSeconds)
                );
            }
        }

        return new RagTriggerValidationResult(true, null);
    }

    /**
     * Trigger RAG indexing for a project.
     * Generates a short-lived project JWT and proxies the request to pipeline-agent.
     *
     * @param workspaceId Workspace ID
     * @param projectId   Project ID
     * @param userId      ID of the user triggering the indexing
     * @param branch      Optional branch to index (null for project default)
     * @param emitter     SSE emitter for streaming progress to frontend
     */
    @Async("ragExecutor")
    public void triggerIndexing(
            Long workspaceId,
            Long projectId,
            Long userId,
            String branch,
            SseEmitter emitter
    ) {
        try {
            // Validate again (double-check)
            RagTriggerValidationResult validation = validateTrigger(projectId, userId);
            if (!validation.valid()) {
                sendError(emitter, validation.message());
                emitter.complete();
                return;
            }

            // Load project
            Project project = projectRepository.findById(projectId)
                    .orElseThrow(() -> new NoSuchElementException("Project not found: " + projectId));

            // Check RAG is enabled for project
            if (project.getConfiguration() == null ||
                    project.getConfiguration().ragConfig() == null ||
                    !project.getConfiguration().ragConfig().enabled()) {
                sendError(emitter, "RAG is not enabled for this project");
                emitter.complete();
                return;
            }

            // Update rate limit tracker
            lastTriggerTimes.put(projectId, Instant.now());

            // Generate short-lived project JWT
            String projectJwt = generateShortLivedProjectJwt(projectId, userId);

            // Start indexing via pipeline-agent
            proxyToPipelineAgent(projectJwt, branch, emitter);

        } catch (NoSuchElementException e) {
            sendError(emitter, e.getMessage());
            emitter.complete();
        } catch (Exception e) {
            log.error("Failed to trigger RAG indexing for project {}", projectId, e);
            sendError(emitter, "Failed to trigger indexing: " + e.getMessage());
            emitter.complete();
        }
    }

    private String generateShortLivedProjectJwt(Long projectId, Long userId) {
        Instant expiresAt = Instant.now().plus(SHORT_LIVED_TOKEN_DURATION_MINUTES, ChronoUnit.MINUTES);
        return jwtUtils.generateJwtTokenForProjectWithUser(
                String.valueOf(projectId),
                String.valueOf(userId),
                Date.from(expiresAt)
        );
    }

    private void proxyToPipelineAgent(String projectJwt, String branch, SseEmitter emitter) {
        String pipelineUrl = getPipelineAgentBaseUrl();
        String indexUrl = pipelineUrl + "/api/rag/index";

        Response response = null;
        try {
            // Build request body
            Map<String, Object> requestBody = branch != null && !branch.isBlank()
                    ? Map.of("branch", branch)
                    : Map.of();

            RequestBody body = RequestBody.create(
                    objectMapper.writeValueAsString(requestBody),
                    MediaType.parse("application/json")
            );

            Request request = new Request.Builder()
                    .url(indexUrl)
                    .header("Authorization", "Bearer " + projectJwt)
                    .header("Accept", "text/event-stream")
                    .post(body)
                    .build();

            // Execute request synchronously and stream response
            response = httpClient.newCall(request).execute();

            if (!response.isSuccessful()) {
                String errorBody = response.body() != null ? response.body().string() : "Unknown error";
                sendError(emitter, "Pipeline agent returned error: " + response.code() + " - " + errorBody);
                emitter.complete();
                return;
            }

            ResponseBody responseBody = response.body();
            if (responseBody == null) {
                sendError(emitter, "Empty response from pipeline agent");
                emitter.complete();
                return;
            }

            // Read SSE stream line by line
            try (BufferedReader reader = new BufferedReader(new InputStreamReader(responseBody.byteStream()))) {
                String line;
                while ((line = reader.readLine()) != null) {
                    if (line.startsWith("data: ")) {
                        String data = line.substring(6).trim();
                        
                        if (EOF_MARKER.equals(data)) {
                            log.debug("Received EOF marker, completing SSE stream");
                            emitter.complete();
                            return;
                        }

                        // Forward event to frontend
                        try {
                            emitter.send(SseEmitter.event()
                                    .name("message")
                                    .data(data));
                        } catch (IOException e) {
                            log.warn("Failed to send SSE event to client (client may have disconnected)", e);
                            return;
                        }
                    }
                    // Ignore other lines (empty lines, comments, event names, etc.)
                }
            }

            // Stream ended normally
            emitter.complete();

        } catch (IOException e) {
            log.error("Failed to communicate with pipeline-agent", e);
            sendError(emitter, "Failed to connect to indexing service: " + e.getMessage());
            try {
                emitter.complete();
            } catch (Exception ignored) {
            }
        } finally {
            if (response != null) {
                response.close();
            }
        }
    }

    private String getPipelineAgentBaseUrl() {
        var urls = siteSettingsProvider.getBaseUrlSettings();
        // Use webhook base URL if configured, otherwise fall back to base URL
        if (urls.webhookBaseUrl() != null && !urls.webhookBaseUrl().isBlank()) {
            return urls.webhookBaseUrl();
        }
        return urls.baseUrl();
    }

    private void sendError(SseEmitter emitter, String message) {
        try {
            Map<String, Object> errorEvent = Map.of(
                    "type", "error",
                    "status", "error",
                    "message", message
            );
            emitter.send(SseEmitter.event()
                    .name("error")
                    .data(objectMapper.writeValueAsString(errorEvent)));
        } catch (IOException e) {
            log.warn("Failed to send error event", e);
        }
    }

    // Validation result record
    public record RagTriggerValidationResult(boolean valid, String message) {}
}
