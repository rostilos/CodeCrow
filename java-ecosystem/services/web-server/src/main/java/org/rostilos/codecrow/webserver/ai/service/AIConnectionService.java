package org.rostilos.codecrow.webserver.ai.service;

import com.fasterxml.jackson.core.JsonProcessingException;
import com.fasterxml.jackson.core.type.TypeReference;
import com.fasterxml.jackson.databind.JsonNode;
import com.fasterxml.jackson.databind.ObjectMapper;
import com.google.auth.oauth2.GoogleCredentials;
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

import java.io.ByteArrayInputStream;
import java.io.IOException;
import java.net.URI;
import java.net.URLEncoder;
import java.nio.charset.StandardCharsets;
import java.security.GeneralSecurityException;
import java.time.Duration;
import java.util.LinkedHashMap;
import java.util.List;
import java.util.Map;
import java.util.NoSuchElementException;

@Service
public class AIConnectionService {
    private static final String GOOGLE_VERTEX_SCOPE = "https://www.googleapis.com/auth/cloud-platform";
    private static final String GOOGLE_VERTEX_DEFAULT_LOCATION =
            System.getenv().getOrDefault(
                    "GOOGLE_VERTEX_LOCATION",
                    System.getenv().getOrDefault("GOOGLE_CLOUD_LOCATION", "global")
            );

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

        validateProviderMetadata(request.providerKey, request.baseUrl);

        AIConnection newAiConnection = new AIConnection();
        String apiKeyEncrypted = tokenEncryptionService.encrypt(request.apiKey);

        newAiConnection.setWorkspace(ws);
        newAiConnection.setName(request.name);
        newAiConnection.setProviderKey(request.providerKey);
        newAiConnection.setAiModel(request.aiModel);
        newAiConnection.setApiKeyEncrypted(apiKeyEncrypted);
        newAiConnection.setBaseUrl(normalizeProviderMetadata(request.providerKey, request.baseUrl));
        newAiConnection.setCustomParameters(normalizeProviderCustomParameters(
                request.providerKey,
                request.customParameters));

        return connectionRepository.save(newAiConnection);
    }

    @Transactional
    public AIConnection updateAiConnection(Long workspaceId, Long connectionId, UpdateAiConnectionRequest request) throws GeneralSecurityException {
        AIConnection connection = connectionRepository.findByWorkspace_IdAndId(workspaceId, connectionId)
                .orElseThrow(() -> new NoSuchElementException("Connection not found"));

        AIProviderKey previousProvider = connection.getProviderKey();
        AIProviderKey effectiveProvider = request.providerKey != null ? request.providerKey : previousProvider;
        String effectiveBaseUrl;
        if (request.baseUrl != null) {
            effectiveBaseUrl = request.baseUrl;
        } else if (request.providerKey != null && request.providerKey != previousProvider) {
            effectiveBaseUrl = null;
        } else {
            effectiveBaseUrl = connection.getBaseUrl();
        }

        validateProviderMetadata(effectiveProvider, effectiveBaseUrl);

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

        connection.setBaseUrl(normalizeProviderMetadata(effectiveProvider, effectiveBaseUrl));
        if (effectiveProvider != AIProviderKey.OPENAI_COMPATIBLE) {
            connection.setCustomParameters(null);
        } else if (request.customParameters != null) {
            connection.setCustomParameters(normalizeCustomParameters(request.customParameters));
        } else if (request.providerKey != null && request.providerKey != previousProvider) {
            connection.setCustomParameters(null);
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

    private TestRequest buildPingRequest(AIConnection connection, String apiKey) throws IOException {
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
            case GOOGLE_VERTEX -> buildGoogleVertexPingRequest(connection, apiKey);
        };
    }

    private TestRequest buildOpenAiCompatiblePingRequest(AIConnection connection, String apiKey)
            throws JsonProcessingException {
        validateProviderMetadata(AIProviderKey.OPENAI_COMPATIBLE, connection.getBaseUrl());
        String baseUrl = normalizeOpenAiCompatibleBaseUrl(connection.getBaseUrl());
        OpenAiCompatibleCustomParameters customParameters = parseOpenAiCompatibleCustomParameters(
                connection.getCustomParameters());
        return buildOpenAiChatRequest(
                baseUrl + "/chat/completions",
                apiKey,
                connection.getAiModel(),
                customParameters.headers(),
                customParameters.bodyParameters()
        );
    }

    private TestRequest buildOpenAiChatRequest(
            String url,
            String apiKey,
            String model,
            Map<String, String> extraHeaders
    ) throws JsonProcessingException {
        return buildOpenAiChatRequest(url, apiKey, model, extraHeaders, Map.of());
    }

    private TestRequest buildOpenAiChatRequest(
            String url,
            String apiKey,
            String model,
            Map<String, String> extraHeaders,
            Map<String, Object> extraBodyParameters
    ) throws JsonProcessingException {
        Map<String, Object> payload = new LinkedHashMap<>(extraBodyParameters);
        payload.put("model", model);
        payload.put("messages", List.of(Map.of(
                "role", "user",
                "content", "ping"
        )));

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

    private TestRequest buildGoogleVertexPingRequest(AIConnection connection, String credentialValue)
            throws IOException {
        VertexProjectLocation projectLocation = parseGoogleVertexProjectLocation(
                connection.getBaseUrl(), credentialValue);
        String model = normalizeGoogleVertexModel(connection.getAiModel());
        String encodedModel = encodePathSegment(model);

        HttpHeaders headers = new HttpHeaders();
        String url;
        if (usesGoogleVertexBearerAuth(credentialValue)) {
            if (projectLocation.project() == null || projectLocation.project().isBlank()) {
                throw new IllegalArgumentException(
                        "Google Vertex requires a project ID in project/location metadata, "
                                + "GOOGLE_VERTEX_PROJECT, or service account JSON");
            }
            headers.setBearerAuth(buildGoogleVertexAccessToken(credentialValue));
            url = "https://aiplatform.googleapis.com/v1/projects/"
                    + encodePathSegment(projectLocation.project())
                    + "/locations/" + encodePathSegment(projectLocation.location())
                    + "/publishers/google/models/" + encodedModel + ":generateContent";
        } else {
            url = "https://aiplatform.googleapis.com/v1/publishers/google/models/"
                    + encodedModel + ":generateContent?key="
                    + URLEncoder.encode(credentialValue, StandardCharsets.UTF_8);
        }

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

        return jsonPost(url, headers, payload);
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

    private String normalizeCustomParameters(String customParameters) {
        if (customParameters == null || customParameters.isBlank()) {
            return null;
        }

        try {
            JsonNode node = objectMapper.readTree(customParameters);
            if (!node.isObject()) {
                throw new IllegalArgumentException("AI custom parameters must be a JSON object");
            }
            return objectMapper.writeValueAsString(node);
        } catch (JsonProcessingException e) {
            throw new IllegalArgumentException("AI custom parameters must be a valid JSON object", e);
        }
    }

    private String normalizeProviderCustomParameters(
            AIProviderKey providerKey,
            String customParameters
    ) {
        if (providerKey != AIProviderKey.OPENAI_COMPATIBLE) {
            return null;
        }
        return normalizeCustomParameters(customParameters);
    }

    private OpenAiCompatibleCustomParameters parseOpenAiCompatibleCustomParameters(String customParameters)
            throws JsonProcessingException {
        if (customParameters == null || customParameters.isBlank()) {
            return OpenAiCompatibleCustomParameters.empty();
        }

        Map<String, Object> raw = objectMapper.readValue(
                customParameters,
                new TypeReference<>() {
                }
        );
        Map<String, Object> bodyParameters = new LinkedHashMap<>();
        Map<String, String> headers = new LinkedHashMap<>();

        mergeOpenAiBodyParameters(bodyParameters, asObjectMap(raw.get("model_kwargs")));
        mergeOpenAiBodyParameters(bodyParameters, asObjectMap(raw.get("extra_body")));
        mergeOpenAiHeaders(headers, asObjectMap(raw.get("default_headers")));

        Map<String, Object> constructorKwargs = asObjectMap(raw.get("constructor_kwargs"));
        if (!constructorKwargs.isEmpty()) {
            mergeOpenAiBodyParameters(bodyParameters, asObjectMap(constructorKwargs.get("extra_body")));
            mergeOpenAiHeaders(headers, asObjectMap(constructorKwargs.get("default_headers")));
        }

        for (Map.Entry<String, Object> entry : raw.entrySet()) {
            String key = entry.getKey();
            if ("model_kwargs".equals(key)
                    || "extra_body".equals(key)
                    || "default_headers".equals(key)
                    || "constructor_kwargs".equals(key)) {
                continue;
            }
            bodyParameters.put(key, entry.getValue());
        }

        bodyParameters.remove("model");
        bodyParameters.remove("messages");
        return new OpenAiCompatibleCustomParameters(headers, bodyParameters);
    }

    private void mergeOpenAiBodyParameters(Map<String, Object> target, Map<String, Object> source) {
        for (Map.Entry<String, Object> entry : source.entrySet()) {
            if (entry.getValue() != null) {
                target.put(entry.getKey(), entry.getValue());
            }
        }
    }

    private void mergeOpenAiHeaders(Map<String, String> target, Map<String, Object> source) {
        for (Map.Entry<String, Object> entry : source.entrySet()) {
            if (entry.getValue() != null) {
                target.put(entry.getKey(), String.valueOf(entry.getValue()));
            }
        }
    }

    private Map<String, Object> asObjectMap(Object value) {
        if (value instanceof Map<?, ?> map) {
            Map<String, Object> normalized = new LinkedHashMap<>();
            for (Map.Entry<?, ?> entry : map.entrySet()) {
                if (entry.getKey() != null) {
                    normalized.put(String.valueOf(entry.getKey()), entry.getValue());
                }
            }
            return normalized;
        }
        return Map.of();
    }

    private String normalizeGoogleVertexModel(String aiModel) {
        String model = aiModel == null ? "" : aiModel.strip();
        String publisherPrefix = "/publishers/google/models/";
        if (model.contains(publisherPrefix)) {
            model = model.substring(model.lastIndexOf(publisherPrefix) + publisherPrefix.length());
        }
        if (model.startsWith("publishers/google/models/")) {
            model = model.substring("publishers/google/models/".length());
        }
        if (model.startsWith("models/")) {
            model = model.substring("models/".length());
        }
        return model;
    }

    private String encodePathSegment(String value) {
        return URLEncoder.encode(value, StandardCharsets.UTF_8).replace("+", "%20");
    }

    private boolean usesGoogleVertexBearerAuth(String credentialValue) {
        String normalized = credentialValue == null ? "" : credentialValue.strip();
        return normalized.startsWith("{") || isGoogleVertexAdcCredential(normalized);
    }

    private boolean isGoogleVertexAdcCredential(String credentialValue) {
        String normalized = credentialValue == null ? "" : credentialValue.strip().toLowerCase();
        return "adc".equals(normalized)
                || "application_default".equals(normalized)
                || "application-default".equals(normalized);
    }

    private String buildGoogleVertexAccessToken(String credentialValue) throws IOException {
        GoogleCredentials credential;
        if (isGoogleVertexAdcCredential(credentialValue)) {
            credential = GoogleCredentials.getApplicationDefault();
        } else {
            credential = GoogleCredentials.fromStream(new ByteArrayInputStream(
                    credentialValue.getBytes(StandardCharsets.UTF_8)));
        }
        credential = credential.createScoped(List.of(GOOGLE_VERTEX_SCOPE));
        credential.refreshIfExpired();
        return credential.getAccessToken().getTokenValue();
    }

    private VertexProjectLocation parseGoogleVertexProjectLocation(String rawValue, String credentialValue) {
        String project = firstNonBlank(
                System.getenv("GOOGLE_VERTEX_PROJECT"),
                System.getenv("GOOGLE_CLOUD_PROJECT"),
                System.getenv("GCLOUD_PROJECT")
        );
        String location = GOOGLE_VERTEX_DEFAULT_LOCATION;

        if (credentialValue != null && credentialValue.strip().startsWith("{")) {
            Map<?, ?> credentialJson = parseJsonObject(credentialValue, "service account JSON");
            project = firstNonBlank(String.valueOf(credentialJson.get("project_id")), project);
        }

        if (rawValue == null || rawValue.isBlank()) {
            return new VertexProjectLocation(project, location);
        }

        String value = rawValue.strip();
        if (value.startsWith("{")) {
            Map<?, ?> json = parseJsonObject(value, "Vertex project/location metadata");
            project = firstNonBlank(
                    String.valueOf(json.get("project")),
                    String.valueOf(json.get("project_id")),
                    project
            );
            location = firstNonBlank(
                    String.valueOf(json.get("location")),
                    String.valueOf(json.get("region")),
                    location
            );
            return new VertexProjectLocation(project, location);
        }

        if (value.startsWith("http://") || value.startsWith("https://")) {
            value = URI.create(value).getPath();
        }

        List<String> parts = List.of(value.replaceAll("^/+|/+$", "").split("/"));
        int projectMarker = parts.indexOf("projects");
        int locationMarker = parts.indexOf("locations");
        if (projectMarker >= 0 && projectMarker + 1 < parts.size()) {
            project = firstNonBlank(parts.get(projectMarker + 1), project);
        }
        if (locationMarker >= 0 && locationMarker + 1 < parts.size()) {
            location = firstNonBlank(parts.get(locationMarker + 1), location);
        }
        if (projectMarker >= 0 || locationMarker >= 0) {
            return new VertexProjectLocation(project, location);
        }

        if (value.contains("/")) {
            String[] split = value.split("/", 2);
            project = firstNonBlank(split[0], project);
            location = firstNonBlank(split[1], location);
            return new VertexProjectLocation(project, location);
        }

        if (value.contains(":")) {
            String[] split = value.split(":", 2);
            project = firstNonBlank(split[0], project);
            location = firstNonBlank(split[1], location);
            return new VertexProjectLocation(project, location);
        }

        return new VertexProjectLocation(firstNonBlank(value, project), location);
    }

    private Map<?, ?> parseJsonObject(String value, String description) {
        try {
            return objectMapper.readValue(value, Map.class);
        } catch (JsonProcessingException e) {
            throw new IllegalArgumentException("Invalid " + description, e);
        }
    }

    private String firstNonBlank(String... values) {
        for (String value : values) {
            if (value != null && !value.isBlank() && !"null".equals(value)) {
                return value.strip();
            }
        }
        return null;
    }

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

    private record OpenAiCompatibleCustomParameters(
            Map<String, String> headers,
            Map<String, Object> bodyParameters
    ) {
        private static OpenAiCompatibleCustomParameters empty() {
            return new OpenAiCompatibleCustomParameters(Map.of(), Map.of());
        }
    }

    private record VertexProjectLocation(String project, String location) {
    }

    /**
     * Validates provider-specific metadata stored in baseUrl.
     */
    private void validateProviderMetadata(AIProviderKey providerKey, String baseUrl) {
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
        } else if (providerKey == AIProviderKey.GOOGLE_VERTEX && baseUrl != null && !baseUrl.isBlank()) {
            VertexProjectLocation projectLocation = parseGoogleVertexProjectLocation(baseUrl, null);
            if (projectLocation.project() == null || projectLocation.project().isBlank()) {
                throw new IllegalArgumentException(
                        "Google Vertex project/location metadata must include a project ID");
            }
        }
    }

    private String normalizeProviderMetadata(AIProviderKey providerKey, String baseUrl) {
        if (providerKey != AIProviderKey.OPENAI_COMPATIBLE && providerKey != AIProviderKey.GOOGLE_VERTEX) {
            return null;
        }
        if (baseUrl == null || baseUrl.isBlank()) {
            return null;
        }
        String normalized = baseUrl.strip();
        if (providerKey == AIProviderKey.OPENAI_COMPATIBLE && normalized.endsWith("/")) {
            return normalized.substring(0, normalized.length() - 1);
        }
        return normalized;
    }
}
