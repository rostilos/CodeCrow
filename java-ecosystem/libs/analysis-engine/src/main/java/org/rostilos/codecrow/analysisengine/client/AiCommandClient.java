package org.rostilos.codecrow.analysisengine.client;

import com.fasterxml.jackson.databind.ObjectMapper;
import org.slf4j.Logger;
import org.slf4j.LoggerFactory;
import org.springframework.beans.factory.annotation.Qualifier;
import org.springframework.beans.factory.annotation.Value;
import org.springframework.http.HttpHeaders;
import org.springframework.http.HttpMethod;
import org.springframework.http.MediaType;
import org.springframework.stereotype.Service;
import org.springframework.web.client.RestClientException;
import org.springframework.web.client.RestTemplate;

import java.io.BufferedReader;
import java.io.IOException;
import java.io.InputStreamReader;
import java.util.Map;
import java.util.function.Consumer;

/**
 * Client for communicating with the AI service for CodeCrow commands (summarize, ask).
 */
@Service
public class AiCommandClient {
    private static final Logger log = LoggerFactory.getLogger(AiCommandClient.class);

    private final RestTemplate restTemplate;
    private final ObjectMapper objectMapper;

    @Value("${codecrow.mcp.client.url:http://host.docker.internal:8000}")
    private String aiClientBaseUrl;

    public AiCommandClient(
            @Qualifier("aiRestTemplate") RestTemplate restTemplate
    ) {
        this.restTemplate = restTemplate;
        this.objectMapper = new ObjectMapper();
    }

    /**
     * Call the summarize endpoint to generate a PR summary.
     *
     * @param request The summarize request containing project, PR, and AI config
     * @param eventHandler Optional consumer for progress events
     * @return SummarizeResult containing summary, diagram, and diagramType
     * @throws IOException if communication fails
     */
    public SummarizeResult summarize(SummarizeRequest request, Consumer<Map<String, Object>> eventHandler)
            throws IOException {
        String url = aiClientBaseUrl + "/summarize";
        log.debug("Sending summarize request to AI client: {}", url);

        try {
            Map<String, Object> response = executeWithStreaming(url, request, eventHandler);
            
            if (response == null) {
                throw new IOException("AI service returned null response");
            }

            // Check for error
            if (response.containsKey("error") && response.get("error") != null) {
                String error = (String) response.get("error");
                log.error("Summarize returned error: {}", error);
                throw new IOException("Summarize failed: " + error);
            }

            return new SummarizeResult(
                (String) response.getOrDefault("summary", ""),
                (String) response.getOrDefault("diagram", ""),
                (String) response.getOrDefault("diagramType", "MERMAID")
            );

        } catch (RestClientException e) {
            log.error("Failed to communicate with AI service for summarize", e);
            throw new IOException("AI service communication failed: " + e.getMessage(), e);
        }
    }

    /**
     * Call the ask endpoint to answer a question.
     *
     * @param request The ask request containing question and context
     * @param eventHandler Optional consumer for progress events
     * @return AskResult containing the answer
     * @throws IOException if communication fails
     */
    public AskResult ask(AskRequest request, Consumer<Map<String, Object>> eventHandler)
            throws IOException {
        String url = aiClientBaseUrl + "/ask";
        log.debug("Sending ask request to AI client: {}", url);

        try {
            Map<String, Object> response = executeWithStreaming(url, request, eventHandler);
            
            if (response == null) {
                throw new IOException("AI service returned null response");
            }

            // Check for error
            if (response.containsKey("error") && response.get("error") != null) {
                String error = (String) response.get("error");
                log.error("Ask returned error: {}", error);
                throw new IOException("Ask failed: " + error);
            }

            return new AskResult((String) response.getOrDefault("answer", ""));

        } catch (RestClientException e) {
            log.error("Failed to communicate with AI service for ask", e);
            throw new IOException("AI service communication failed: " + e.getMessage(), e);
        }
    }

    /**
     * Call the review endpoint to generate a code review.
     *
     * @param request The review request containing project, PR, and AI config
     * @param eventHandler Optional consumer for progress events
     * @return ReviewResult containing the review text
     * @throws IOException if communication fails
     */
    public ReviewResult review(ReviewRequest request, Consumer<Map<String, Object>> eventHandler)
            throws IOException {
        String url = aiClientBaseUrl + "/review";
        log.debug("Sending review request to AI client: {}", url);

        try {
            Map<String, Object> response = executeWithStreaming(url, request, eventHandler);
            
            if (response == null) {
                throw new IOException("AI service returned null response");
            }

            // Check for error
            if (response.containsKey("error") && response.get("error") != null) {
                String error = (String) response.get("error");
                log.error("Review returned error: {}", error);
                throw new IOException("Review failed: " + error);
            }

            return new ReviewResult((String) response.getOrDefault("review", ""));

        } catch (RestClientException e) {
            log.error("Failed to communicate with AI service for review", e);
            throw new IOException("AI service communication failed: " + e.getMessage(), e);
        }
    }

    /**
     * Execute a request with NDJSON streaming support.
     */
    @SuppressWarnings("unchecked")
    private Map<String, Object> executeWithStreaming(
            String url,
            Object request,
            Consumer<Map<String, Object>> eventHandler
    ) {
        return restTemplate.execute(url, HttpMethod.POST,
            clientHttpRequest -> {
                clientHttpRequest.getHeaders().setContentType(MediaType.APPLICATION_JSON);
                clientHttpRequest.getHeaders().set(HttpHeaders.ACCEPT, "application/x-ndjson");
                objectMapper.writeValue(clientHttpRequest.getBody(), request);
            },
            clientHttpResponse -> {
                MediaType ct = clientHttpResponse.getHeaders().getContentType();
                String ctValue = ct != null ? ct.toString() : "";

                if (ctValue.contains("ndjson") || ctValue.contains("application/x-ndjson")) {
                    // Parse NDJSON stream
                    BufferedReader reader = new BufferedReader(
                        new InputStreamReader(clientHttpResponse.getBody())
                    );
                    String line;
                    Map<String, Object> finalResult = null;

                    while ((line = reader.readLine()) != null) {
                        if (line.isBlank()) continue;
                        try {
                            Map<String, Object> event = objectMapper.readValue(line, Map.class);

                            // Forward event to caller
                            if (eventHandler != null) {
                                try {
                                    eventHandler.accept(event);
                                } catch (Exception ex) {
                                    log.warn("Event handler threw exception: {}", ex.getMessage());
                                }
                            }

                            // Capture final result
                            Object type = event.get("type");
                            if ("final".equals(type) || "result".equals(type)) {
                                Object res = event.get("result");
                                if (res instanceof Map) {
                                    finalResult = (Map<String, Object>) res;
                                } else if (res != null) {
                                    finalResult = Map.of("result", res);
                                }
                            }
                        } catch (Exception ex) {
                            log.warn("Failed to parse NDJSON event: {}", ex.getMessage());
                        }
                    }
                    return finalResult;
                }

                // Parse as regular JSON
                try {
                    return objectMapper.readValue(clientHttpResponse.getBody(), Map.class);
                } catch (Exception ex) {
                    log.warn("Failed to parse response body: {}", ex.getMessage());
                    return null;
                }
            });
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
        String vcsProvider
    ) {}

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
        java.util.List<String> issueReferences
    ) {}

    /**
     * Result from summarize endpoint.
     */
    public record SummarizeResult(
        String summary,
        String diagram,
        String diagramType
    ) {}

    /**
     * Result from ask endpoint.
     */
    public record AskResult(
        String answer
    ) {}

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
        String vcsProvider
    ) {}

    /**
     * Result from review endpoint.
     */
    public record ReviewResult(
        String review
    ) {}
}
