package org.rostilos.codecrow.analysisengine.client;

import org.rostilos.codecrow.analysisengine.dto.request.ai.AiAnalysisRequest;
import org.slf4j.Logger;
import org.slf4j.LoggerFactory;
import org.springframework.beans.factory.annotation.Qualifier;
import org.springframework.beans.factory.annotation.Value;
import org.springframework.http.HttpHeaders;
import org.springframework.http.MediaType;
import org.springframework.stereotype.Service;
import org.springframework.web.client.RestClientException;
import org.springframework.web.client.RestTemplate;

import java.io.IOException;
import java.security.GeneralSecurityException;
import java.util.List;
import java.util.Map;

/**
 * Client for communicating with the AI analysis service (MCP client).
 */
@Service
public class AiAnalysisClient {
    private static final Logger log = LoggerFactory.getLogger(AiAnalysisClient.class);

    private final RestTemplate restTemplate;

    @Value("${codecrow.mcp.client.url:http://host.docker.internal:8000/review}")
    private String aiClientUrl;

    public AiAnalysisClient(
            @Qualifier("aiRestTemplate") RestTemplate restTemplate
    ) {
        this.restTemplate = restTemplate;
        this.restTemplate.getInterceptors().add((request, body, execution) -> {
            // Wipe out whatever Spring wants to send
            request.getHeaders().set(HttpHeaders.ACCEPT, "application/x-ndjson");
            return execution.execute(request, body);
        });
    }

    /**
     * Performs code analysis by calling the AI service (legacy synchronous call).
     *
     * @param request The analysis request with encrypted credentials
     * @return Map containing analysis results with 'comment' and 'issues' keys
     * @throws IOException if the AI service returns invalid response
     * @throws GeneralSecurityException if credential decryption fails
     */
    public Map<String, Object> performAnalysis(AiAnalysisRequest request)
            throws IOException, GeneralSecurityException {
        // Delegate to the internal implementation without an event handler.
        return performAnalysis(request, null);
    }

    /**
     * Performs code analysis by calling the AI service and optionally receiving intermediate events.
     * If the AI service returns an NDJSON stream, each parsed event will be passed to the eventHandler.
     * The method still returns the final validated result map.
     *
     * @param request      The analysis request
     * @param eventHandler optional consumer that will be invoked for every parsed event (may be null)
     * @return Map containing analysis result (comment + issues)
     * @throws IOException on communication / parsing errors
     * @throws GeneralSecurityException if credential decryption fails
     */
    public Map<String, Object> performAnalysis(AiAnalysisRequest request, java.util.function.Consumer<Map<String,Object>> eventHandler)
            throws IOException, GeneralSecurityException {

        try {
            log.debug("Sending analysis request to AI client: {}", aiClientUrl);

            // Try streaming-first: request Accept: application/x-ndjson and parse NDJSON if returned.
            com.fasterxml.jackson.databind.ObjectMapper mapper = new com.fasterxml.jackson.databind.ObjectMapper();

            try {
                Map streamedResult = restTemplate.execute(aiClientUrl, org.springframework.http.HttpMethod.POST,
                        clientHttpRequest -> {
                            // Set headers
                            clientHttpRequest.getHeaders().setContentType(MediaType.APPLICATION_JSON);
                            // Write request body as JSON
                            mapper.writeValue(clientHttpRequest.getBody(), request);
                        },
                        clientHttpResponse -> {
                            org.springframework.http.MediaType ct = clientHttpResponse.getHeaders().getContentType();
                            String ctValue = ct != null ? ct.toString() : "";

                            // If server returned NDJSON, parse line-by-line and capture final/result
                            if (ctValue.contains("ndjson") || ctValue.contains("application/x-ndjson")) {
                                java.io.BufferedReader reader = new java.io.BufferedReader(new java.io.InputStreamReader(clientHttpResponse.getBody()));
                                String line;
                                Map finalResult = null;
                                while ((line = reader.readLine()) != null) {
                                    if (line.isBlank()) continue;
                                    try {
                                        Map event = mapper.readValue(line, Map.class);

                                        // Forward event to caller if handler provided
                                        if (eventHandler != null) {
                                            try {
                                                eventHandler.accept(event);
                                            } catch (Exception ex) {
                                                log.warn("Event handler threw exception: {}", ex.getMessage());
                                            }
                                        }

                                        Object type = event.get("type");
                                        if ("final".equals(type) || "result".equals(type)) {
                                            Object res = event.get("result");
                                            if (res instanceof Map) {
                                                finalResult = (Map) res;
                                            } else if (res != null) {
                                                finalResult = Map.of("result", res);
                                            }
                                            // keep reading to drain stream
                                        }
                                    } catch (Exception ex) {
                                        log.warn("Failed to parse NDJSON event line: {}", ex.getMessage());
                                    }
                                }
                                return finalResult;
                            }

                            // Otherwise, try to parse whole body as JSON (legacy)
                            try {
                                return mapper.readValue(clientHttpResponse.getBody(), Map.class);
                            } catch (Exception ex) {
                                log.warn("Failed to parse non-ndjson response body: {}", ex.getMessage());
                                return null;
                            }
                        });

                if (streamedResult != null) {
                    log.info("AI client streaming/json response received");
                    return extractAndValidateAnalysisData(streamedResult);
                } else {
                    // Fall back to simple postForObject if streaming attempt returned null
                    log.debug("Streaming attempt returned null, falling back to postForObject");
                    Map response = restTemplate.postForObject(aiClientUrl, request, Map.class);
                    if (response == null) {
                        throw new IOException("AI service returned null response");
                    }
                    return extractAndValidateAnalysisData(response);
                }

            } catch (RestClientException e) {
                log.warn("Streaming attempt failed, falling back to postForObject: {}", e.getMessage());
                Map response = restTemplate.postForObject(aiClientUrl, request, Map.class);
                if (response == null) {
                    throw new IOException("AI service returned null response");
                }
                return extractAndValidateAnalysisData(response);
            }

        } catch (RestClientException e) {
            log.error("Failed to communicate with AI service", e);
            throw new IOException("AI service communication failed: " + e.getMessage(), e);
        }
    }

    /**
     * Extracts analysis data from nested response structure.
     * Expected: response -> result -> {comment, issues}
     * Issues can be either a List (array) or Map (object with numeric keys)
     */
    private Map<String, Object> extractAndValidateAnalysisData(Map response) throws IOException {
        try {
            Map result;

            if (response.get("issues") != null && response.get("comment") != null) {
                result = response;
            } else {
                result = (Map) response.get("result");
            }

            if (result == null) {
                throw new IOException("Missing 'result' field in AI response");
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
