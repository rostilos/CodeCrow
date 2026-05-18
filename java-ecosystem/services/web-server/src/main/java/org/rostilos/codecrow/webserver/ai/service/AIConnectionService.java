package org.rostilos.codecrow.webserver.ai.service;

import com.fasterxml.jackson.core.JsonProcessingException;
import com.fasterxml.jackson.databind.ObjectMapper;
import jakarta.persistence.EntityManager;
import jakarta.persistence.PersistenceContext;
import org.rostilos.codecrow.core.model.ai.AIConnection;
import org.rostilos.codecrow.core.model.ai.AIProviderKey;
import org.rostilos.codecrow.core.model.workspace.Workspace;
import org.rostilos.codecrow.core.persistence.repository.ai.AiConnectionRepository;
import org.rostilos.codecrow.core.persistence.repository.workspace.WorkspaceRepository;
import org.rostilos.codecrow.core.util.SsrfSafeUrlValidator;
import org.rostilos.codecrow.security.oauth.TokenEncryptionService;
import org.rostilos.codecrow.webserver.ai.dto.request.CreateAIConnectionRequest;
import org.rostilos.codecrow.webserver.ai.dto.request.UpdateAiConnectionRequest;
import org.rostilos.codecrow.webserver.ai.dto.response.AIConnectionTestResponse;
import org.springframework.http.HttpEntity;
import org.springframework.http.HttpHeaders;
import org.springframework.http.HttpMethod;
import org.springframework.http.ResponseEntity;
import org.springframework.stereotype.Service;
import org.springframework.transaction.annotation.Transactional;
import org.springframework.web.client.RestClientResponseException;
import org.springframework.web.client.RestTemplate;

import java.io.IOException;
import java.net.URI;
import java.net.URLEncoder;
import java.nio.charset.StandardCharsets;
import java.security.GeneralSecurityException;
import java.time.Duration;
import java.util.List;
import java.util.Map;
import java.util.NoSuchElementException;

@Service
public class AIConnectionService {
    @PersistenceContext
    private EntityManager entityManager;

    private final AiConnectionRepository connectionRepository;
    private final TokenEncryptionService tokenEncryptionService;
    private final WorkspaceRepository workspaceRepository;
    private final ObjectMapper objectMapper;
    private final RestTemplate restTemplate;

    public AIConnectionService(
            AiConnectionRepository connectionRepository,
            TokenEncryptionService tokenEncryptionService,
            WorkspaceRepository workspaceRepository,
            ObjectMapper objectMapper,
            RestTemplate restTemplate
    ) {
        this.connectionRepository = connectionRepository;
        this.tokenEncryptionService = tokenEncryptionService;
        this.workspaceRepository = workspaceRepository;
        this.objectMapper = objectMapper;
        this.restTemplate = restTemplate;
    }

    @Transactional(readOnly = true)
    public List<AIConnection> listWorkspaceConnections(Long workspaceId) {
        return connectionRepository.findByWorkspace_Id(workspaceId);
    }

    @Transactional
    public AIConnection createAiConnection(Long workspaceId, CreateAIConnectionRequest request) throws GeneralSecurityException {
        Workspace ws = workspaceRepository.findById(workspaceId)
                .orElseThrow(() -> new NoSuchElementException("Workspace not found"));

        // Validate baseUrl for OPENAI_COMPATIBLE provider
        validateBaseUrl(request.providerKey, request.baseUrl);

        AIConnection newAiConnection = new AIConnection();
        String apiKeyEncrypted = tokenEncryptionService.encrypt(request.apiKey);

        newAiConnection.setWorkspace(ws);
        newAiConnection.setName(request.name);
        newAiConnection.setProviderKey(request.providerKey);
        newAiConnection.setAiModel(request.aiModel);
        newAiConnection.setApiKeyEncrypted(apiKeyEncrypted);
        newAiConnection.setBaseUrl(
                request.providerKey == AIProviderKey.OPENAI_COMPATIBLE ? request.baseUrl : null
        );

        return connectionRepository.save(newAiConnection);
    }

    @Transactional
    public AIConnection updateAiConnection(Long workspaceId, Long connectionId, UpdateAiConnectionRequest request) throws GeneralSecurityException {
        AIConnection connection = connectionRepository.findByWorkspace_IdAndId(workspaceId, connectionId)
                .orElseThrow(() -> new NoSuchElementException("Connection not found"));

        if (request.name != null) {
            connection.setName(request.name);
        }
        if (request.providerKey != null) {
            connection.setProviderKey(request.providerKey);
        }
        if(request.aiModel != null && !request.aiModel.isEmpty()) {
            connection.setAiModel(request.aiModel);
        }
        if(request.apiKey != null && !request.apiKey.isEmpty()) {
            String apiKeyEncrypted = tokenEncryptionService.encrypt(request.apiKey);
            connection.setApiKeyEncrypted(apiKeyEncrypted);
        }

        // Handle baseUrl: validate and set for OPENAI_COMPATIBLE, clear for other providers
        AIProviderKey effectiveProvider = request.providerKey != null
                ? request.providerKey : connection.getProviderKey();
        if (effectiveProvider == AIProviderKey.OPENAI_COMPATIBLE) {
            String effectiveBaseUrl = request.baseUrl != null ? request.baseUrl : connection.getBaseUrl();
            validateBaseUrl(effectiveProvider, effectiveBaseUrl);
            if (request.baseUrl != null) {
                connection.setBaseUrl(request.baseUrl);
            }
        } else {
            connection.setBaseUrl(null);
        }

        return connectionRepository.save(connection);
    }


    @Transactional
    public void deleteAiConnection(Long workspaceId, Long connectionId) {
        AIConnection connection = connectionRepository.findByWorkspace_IdAndId(workspaceId, connectionId)
                .orElseThrow(() -> new NoSuchElementException("Project not found"));
        connectionRepository.delete(connection);
    }

    @Transactional(readOnly = true)
    public AIConnectionTestResponse testAiConnection(Long workspaceId, Long connectionId) throws GeneralSecurityException {
        AIConnection connection = connectionRepository.findByWorkspace_IdAndId(workspaceId, connectionId)
                .orElseThrow(() -> new NoSuchElementException("Connection not found"));
        String apiKey = tokenEncryptionService.decrypt(connection.getApiKeyEncrypted());

        try {
            TestRequest request = buildPingRequest(connection, apiKey);
            long started = System.nanoTime();
            ResponseEntity<String> response = restTemplate.exchange(
                    URI.create(request.url()),
                    HttpMethod.POST,
                    new HttpEntity<>(request.body(), request.headers()),
                    String.class
            );
            long latencyMs = Duration.ofNanos(System.nanoTime() - started).toMillis();

            if (response.getStatusCode().is2xxSuccessful()) {
                if (hasProviderError(response.getBody())) {
                    return new AIConnectionTestResponse(
                            false,
                            "Endpoint responded with an error: " + extractErrorMessage(response.getBody()),
                            response.getStatusCode().value(),
                            latencyMs
                    );
                }
                return new AIConnectionTestResponse(
                        true,
                        "Endpoint responded successfully.",
                        response.getStatusCode().value(),
                        latencyMs
                );
            }

            return new AIConnectionTestResponse(
                    false,
                    "Endpoint returned HTTP " + response.getStatusCode().value() + ": " + extractErrorMessage(response.getBody()),
                    response.getStatusCode().value(),
                    latencyMs
            );
        } catch (RestClientResponseException e) {
            return new AIConnectionTestResponse(
                    false,
                    "Endpoint returned HTTP " + e.getStatusCode().value() + ": " + extractErrorMessage(e.getResponseBodyAsString()),
                    e.getStatusCode().value(),
                    0
            );
        } catch (IOException | RuntimeException e) {
            return new AIConnectionTestResponse(false, "Connection test failed: " + e.getMessage(), 0, 0);
        }
    }

    private TestRequest buildPingRequest(AIConnection connection, String apiKey) throws JsonProcessingException {
        AIProviderKey provider = connection.getProviderKey();

        return switch (provider) {
            case OPENAI -> buildOpenAiChatRequest(
                    "https://api.openai.com/v1/chat/completions",
                    apiKey,
                    connection.getAiModel(),
                    Map.of()
            );
            case OPENROUTER -> buildOpenAiChatRequest(
                    "https://openrouter.ai/api/v1/chat/completions",
                    apiKey,
                    connection.getAiModel(),
                    Map.of(
                            "HTTP-Referer", "https://codecrow.cloud",
                            "X-Title", "CodeCrow AI"
                    )
            );
            case OPENAI_COMPATIBLE -> buildOpenAiCompatiblePingRequest(connection, apiKey);
            case ANTHROPIC -> buildAnthropicPingRequest(connection, apiKey);
            case GOOGLE -> buildGooglePingRequest(connection, apiKey);
        };
    }

    private TestRequest buildOpenAiCompatiblePingRequest(AIConnection connection, String apiKey)
            throws JsonProcessingException {
        validateBaseUrl(AIProviderKey.OPENAI_COMPATIBLE, connection.getBaseUrl());
        String baseUrl = normalizeOpenAiCompatibleBaseUrl(connection.getBaseUrl());
        return buildOpenAiChatRequest(
                baseUrl + "/chat/completions",
                apiKey,
                connection.getAiModel(),
                Map.of()
        );
    }

    private TestRequest buildOpenAiChatRequest(
            String url,
            String apiKey,
            String model,
            Map<String, String> extraHeaders
    ) throws JsonProcessingException {
        Map<String, Object> payload = Map.of(
                "model", model,
                "messages", List.of(Map.of(
                        "role", "user",
                        "content", "ping"
                ))
        );

        HttpHeaders headers = new HttpHeaders();
        headers.setBearerAuth(apiKey);
        extraHeaders.forEach(headers::set);
        return jsonPost(url, headers, payload);
    }

    private TestRequest buildAnthropicPingRequest(AIConnection connection, String apiKey)
            throws JsonProcessingException {
        Map<String, Object> payload = Map.of(
                "model", connection.getAiModel(),
                "max_tokens", 8,
                "messages", List.of(Map.of(
                        "role", "user",
                        "content", "ping"
                ))
        );

        HttpHeaders headers = new HttpHeaders();
        headers.set("x-api-key", apiKey);
        headers.set("anthropic-version", "2023-06-01");
        return jsonPost("https://api.anthropic.com/v1/messages", headers, payload);
    }

    private TestRequest buildGooglePingRequest(AIConnection connection, String apiKey)
            throws JsonProcessingException {
        String model = connection.getAiModel();
        if (model.startsWith("models/")) {
            model = model.substring("models/".length());
        }

        String encodedModel = URLEncoder.encode(model, StandardCharsets.UTF_8);
        String encodedKey = URLEncoder.encode(apiKey, StandardCharsets.UTF_8);
        String url = "https://generativelanguage.googleapis.com/v1beta/models/"
                + encodedModel + ":generateContent?key=" + encodedKey;

        Map<String, Object> payload = Map.of(
                "contents", List.of(Map.of(
                        "role", "user",
                        "parts", List.of(Map.of("text", "ping"))
                )),
                "generationConfig", Map.of(
                        "maxOutputTokens", 8,
                        "temperature", 0
                )
        );

        return jsonPost(url, new HttpHeaders(), payload);
    }

    private TestRequest jsonPost(String url, HttpHeaders headers, Map<String, Object> payload)
            throws JsonProcessingException {
        headers.set("Content-Type", "application/json");
        headers.set("Accept", "application/json");
        return new TestRequest(url, headers, objectMapper.writeValueAsString(payload));
    }

    private String normalizeOpenAiCompatibleBaseUrl(String aiBaseUrl) {
        String baseUrl = trimOpenAiEndpointSuffix(aiBaseUrl.strip().replaceAll("/+$", ""));
        URI uri = URI.create(baseUrl);
        String host = uri.getHost() == null ? "" : uri.getHost().toLowerCase();
        String path = uri.getPath() == null ? "" : uri.getPath();

        if ("api.cloudflare.com".equals(host) || host.endsWith(".ai.cloudflare.com")) {
            if ("api.cloudflare.com".equals(host) && path.endsWith("/ai")) {
                return baseUrl + "/v1";
            }
            return baseUrl;
        }

        if (!baseUrl.endsWith("/v1")) {
            return baseUrl + "/v1";
        }
        return baseUrl;
    }

    private String trimOpenAiEndpointSuffix(String baseUrl) {
        List<String> suffixes = List.of(
                "/chat/completions",
                "/completions",
                "/embeddings",
                "/responses"
        );
        for (String suffix : suffixes) {
            if (baseUrl.endsWith(suffix)) {
                return baseUrl.substring(0, baseUrl.length() - suffix.length());
            }
        }
        return baseUrl;
    }

    @SuppressWarnings("unchecked")
    private boolean hasProviderError(String body) {
        try {
            Object parsed = objectMapper.readValue(body, Object.class);
            if (parsed instanceof Map<?, ?> map) {
                Object success = map.get("success");
                Object error = map.get("error");
                Object errors = map.get("errors");
                return Boolean.FALSE.equals(success) || error != null || hasNonEmptyList(errors);
            }
        } catch (Exception ignored) {
            return false;
        }
        return false;
    }

    private boolean hasNonEmptyList(Object value) {
        return value instanceof List<?> list && !list.isEmpty();
    }

    private String extractErrorMessage(String body) {
        if (body == null || body.isBlank()) {
            return "No response body";
        }

        try {
            Object parsed = objectMapper.readValue(body, Object.class);
            if (parsed instanceof Map<?, ?> map) {
                Object error = map.get("error");
                if (error instanceof Map<?, ?> errorMap && errorMap.get("message") != null) {
                    return truncate(String.valueOf(errorMap.get("message")));
                }
                if (error instanceof String errorString) {
                    return truncate(errorString);
                }

                Object errors = map.get("errors");
                if (errors instanceof List<?> list && !list.isEmpty()) {
                    Object first = list.get(0);
                    if (first instanceof Map<?, ?> firstError && firstError.get("message") != null) {
                        return truncate(String.valueOf(firstError.get("message")));
                    }
                    return truncate(String.valueOf(first));
                }

                Object message = map.get("message");
                if (message != null) {
                    return truncate(String.valueOf(message));
                }
            }
        } catch (Exception ignored) {
            // Fall back to raw body below.
        }

        return truncate(body);
    }

    private String truncate(String value) {
        String sanitized = value.replaceAll("[\\r\\n\\t]+", " ").strip();
        if (sanitized.length() <= 500) {
            return sanitized;
        }
        return sanitized.substring(0, 500) + "...";
    }

    private record TestRequest(String url, HttpHeaders headers, String body) {
    }

    /**
     * Validates baseUrl for OPENAI_COMPATIBLE connections.
     * Enforces HTTPS, valid URL format, and rejects private/reserved IPs (SSRF protection).
     */
    private void validateBaseUrl(AIProviderKey providerKey, String baseUrl) {
        if (providerKey == AIProviderKey.OPENAI_COMPATIBLE) {
            if (baseUrl == null || baseUrl.isBlank()) {
                throw new IllegalArgumentException(
                        "Base URL is required for OpenAI Compatible provider");
            }
            // Strip trailing slash for consistency
            String normalized = baseUrl.strip();
            if (normalized.endsWith("/")) {
                normalized = normalized.substring(0, normalized.length() - 1);
            }
            SsrfSafeUrlValidator.validate(normalized);
        }
    }
}
