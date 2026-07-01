package org.rostilos.codecrow.webserver.project.service;

import com.fasterxml.jackson.core.type.TypeReference;
import com.fasterxml.jackson.databind.ObjectMapper;
import okhttp3.MediaType;
import okhttp3.OkHttpClient;
import okhttp3.Request;
import okhttp3.RequestBody;
import okhttp3.Response;
import org.rostilos.codecrow.core.model.project.Project;
import org.rostilos.codecrow.core.model.workspace.Workspace;
import org.slf4j.Logger;
import org.slf4j.LoggerFactory;
import org.springframework.beans.factory.annotation.Value;
import org.springframework.stereotype.Service;

import java.io.IOException;
import java.net.URLEncoder;
import java.nio.charset.StandardCharsets;
import java.util.ArrayList;
import java.util.LinkedHashMap;
import java.util.List;
import java.util.Map;
import java.util.concurrent.TimeUnit;

@Service
public class VectorStorageService {
    private static final Logger log = LoggerFactory.getLogger(VectorStorageService.class);
    private static final MediaType JSON = MediaType.parse("application/json; charset=utf-8");
    private static final TypeReference<Map<String, Object>> MAP_TYPE = new TypeReference<>() {
    };

    private final OkHttpClient httpClient;
    private final ObjectMapper objectMapper;
    private final String ragApiUrl;
    private final boolean ragEnabled;
    private final String serviceSecret;

    public VectorStorageService(
            ObjectMapper objectMapper,
            @Value("${codecrow.rag.api.url:http://rag-pipeline:8001}") String ragApiUrl,
            @Value("${codecrow.rag.api.enabled:true}") boolean ragEnabled,
            @Value("${codecrow.rag.api.timeout.connect:30}") int connectTimeout,
            @Value("${codecrow.rag.api.timeout.read:120}") int readTimeout,
            @Value("${codecrow.rag.api.secret:}") String serviceSecret) {
        this.objectMapper = objectMapper;
        this.ragApiUrl = normalizeBaseUrl(ragApiUrl);
        this.ragEnabled = ragEnabled;
        this.serviceSecret = serviceSecret != null ? serviceSecret : "";
        this.httpClient = new OkHttpClient.Builder()
                .connectTimeout(connectTimeout, TimeUnit.SECONDS)
                .readTimeout(readTimeout, TimeUnit.SECONDS)
                .writeTimeout(readTimeout, TimeUnit.SECONDS)
                .build();
    }

    public Map<String, Object> getOverview(Workspace workspace, Project project) {
        if (!ragEnabled) {
            return unavailable("RAG API is disabled on this server");
        }

        String url = baseInspectUrl(workspace, project) + "/overview?sample_limit=10000";
        try {
            return get(url);
        } catch (IOException e) {
            log.warn("Failed to load vector storage overview for project {}: {}", project.getId(), e.getMessage());
            return unavailable("Vector storage service is unavailable");
        }
    }

    public Map<String, Object> getGraph(Workspace workspace, Project project, Map<String, Object> request) {
        if (!ragEnabled) {
            return unavailableGraph("RAG API is disabled on this server");
        }

        Map<String, Object> payload = sanitizeGraphRequest(request);
        String url = baseInspectUrl(workspace, project) + "/graph";
        try {
            return post(url, payload);
        } catch (IOException e) {
            log.warn("Failed to load vector storage graph for project {}: {}", project.getId(), e.getMessage());
            return unavailableGraph("Vector storage service is unavailable");
        }
    }

    public Map<String, Object> getPoint(Workspace workspace, Project project, String pointId, Map<String, Object> request) {
        if (!ragEnabled) {
            return unavailable("RAG API is disabled on this server");
        }

        Map<String, Object> payload = sanitizeNodeRequest(request);
        String url = baseInspectUrl(workspace, project) + "/points/" + encodePath(pointId);
        try {
            return post(url, payload);
        } catch (IOException e) {
            log.warn("Failed to load vector storage point {} for project {}: {}", pointId, project.getId(), e.getMessage());
            return unavailable("Vector point is unavailable");
        }
    }

    private Map<String, Object> sanitizeGraphRequest(Map<String, Object> raw) {
        Map<String, Object> payload = new LinkedHashMap<>();
        payload.put("filters", sanitizeFilters(asMap(raw != null ? raw.get("filters") : null)));
        payload.put("limit", clampNumber(raw != null ? raw.get("limit") : null, 160, 20, 5000));
        payload.put("scan_limit", clampNumber(firstPresent(raw, "scanLimit", "scan_limit"), 2500, 100, 100000));

        String cursor = sanitizeString(raw != null ? raw.get("cursor") : null, 256);
        if (cursor != null) {
            payload.put("cursor", cursor);
        }
        return payload;
    }

    private Map<String, Object> sanitizeNodeRequest(Map<String, Object> raw) {
        Map<String, Object> payload = new LinkedHashMap<>();
        payload.put("filters", sanitizeFilters(asMap(raw != null ? raw.get("filters") : null)));
        payload.put("neighbor_limit", clampNumber(firstPresent(raw, "neighborLimit", "neighbor_limit"), 80, 10, 160));
        return payload;
    }

    private Map<String, Object> sanitizeFilters(Map<String, Object> rawFilters) {
        Map<String, Object> filters = new LinkedHashMap<>();
        filters.put("branches", sanitizeStringList(rawFilters != null ? rawFilters.get("branches") : null, 20, 200));
        filters.put("languages", sanitizeStringList(rawFilters != null ? rawFilters.get("languages") : null, 20, 80));

        putIfNotNull(filters, "path", sanitizeString(rawFilters != null ? rawFilters.get("path") : null, 500));
        putIfNotNull(filters, "file_query", sanitizeString(firstPresent(rawFilters, "fileQuery", "file_query"), 500));
        putIfNotNull(filters, "semantic_query", sanitizeString(firstPresent(rawFilters, "semanticQuery", "semantic_query"), 160));

        Integer prNumber = optionalPositiveNumber(firstPresent(rawFilters, "prNumber", "pr_number"));
        if (prNumber != null) {
            filters.put("pr_number", prNumber);
        }

        Object includePr = firstPresent(rawFilters, "includePr", "include_pr");
        filters.put("include_pr", includePr instanceof Boolean ? includePr : Boolean.TRUE);
        return filters;
    }

    private Map<String, Object> get(String url) throws IOException {
        Request.Builder builder = new Request.Builder().url(url).get();
        addAuthHeader(builder);
        return execute(builder.build());
    }

    private Map<String, Object> post(String url, Map<String, Object> payload) throws IOException {
        String json = objectMapper.writeValueAsString(payload);
        RequestBody body = RequestBody.create(json, JSON);
        Request.Builder builder = new Request.Builder().url(url).post(body);
        addAuthHeader(builder);
        return execute(builder.build());
    }

    private Map<String, Object> execute(Request request) throws IOException {
        try (Response response = httpClient.newCall(request).execute()) {
            String responseBody = response.body() != null ? response.body().string() : "{}";
            if (!response.isSuccessful()) {
                throw new IOException("RAG API returned " + response.code());
            }
            return objectMapper.readValue(responseBody, MAP_TYPE);
        }
    }

    private String baseInspectUrl(Workspace workspace, Project project) {
        // RAG indexing stores project collections by workspace name, not the public route slug.
        // The workspace/project are already resolved and authorized server-side before this call.
        return ragApiUrl + "/inspect/" + encodePath(workspace.getName()) + "/" + encodePath(project.getNamespace());
    }

    private void addAuthHeader(Request.Builder builder) {
        if (!serviceSecret.isBlank()) {
            builder.addHeader("x-service-secret", serviceSecret);
        }
    }

    private static String normalizeBaseUrl(String baseUrl) {
        if (baseUrl == null || baseUrl.isBlank()) {
            return "";
        }
        int end = baseUrl.length();
        while (end > 0 && baseUrl.charAt(end - 1) == '/') {
            end--;
        }
        return end == baseUrl.length() ? baseUrl : baseUrl.substring(0, end);
    }

    private static String encodePath(String value) {
        return URLEncoder.encode(value, StandardCharsets.UTF_8).replace("+", "%20");
    }

    @SuppressWarnings("unchecked")
    private static Map<String, Object> asMap(Object value) {
        if (value instanceof Map<?, ?> map) {
            return (Map<String, Object>) map;
        }
        return Map.of();
    }

    private static Object firstPresent(Map<String, Object> map, String first, String second) {
        if (map == null) {
            return null;
        }
        return map.containsKey(first) ? map.get(first) : map.get(second);
    }

    private static Integer clampNumber(Object value, int defaultValue, int min, int max) {
        Integer number = optionalPositiveNumber(value);
        if (number == null) {
            number = defaultValue;
        }
        return Math.max(min, Math.min(max, number));
    }

    private static Integer optionalPositiveNumber(Object value) {
        if (value instanceof Number number) {
            int candidate = number.intValue();
            return candidate > 0 ? candidate : null;
        }
        if (value instanceof String stringValue) {
            try {
                int candidate = Integer.parseInt(stringValue);
                return candidate > 0 ? candidate : null;
            } catch (NumberFormatException ignored) {
                return null;
            }
        }
        return null;
    }

    private static String sanitizeString(Object value, int maxLength) {
        if (!(value instanceof String stringValue)) {
            return null;
        }
        String trimmed = stringValue.trim();
        if (trimmed.isEmpty()) {
            return null;
        }
        return trimmed.length() > maxLength ? trimmed.substring(0, maxLength) : trimmed;
    }

    private static List<String> sanitizeStringList(Object value, int maxItems, int maxLength) {
        if (!(value instanceof List<?> rawList)) {
            return List.of();
        }

        List<String> sanitized = new ArrayList<>();
        for (Object item : rawList) {
            String clean = sanitizeString(item, maxLength);
            if (clean != null && !sanitized.contains(clean)) {
                sanitized.add(clean);
            }
            if (sanitized.size() >= maxItems) {
                break;
            }
        }
        return sanitized;
    }

    private static void putIfNotNull(Map<String, Object> target, String key, Object value) {
        if (value != null) {
            target.put(key, value);
        }
    }

    private static Map<String, Object> unavailable(String reason) {
        return Map.of(
                "available", false,
                "reason", reason);
    }

    private static Map<String, Object> unavailableGraph(String reason) {
        Map<String, Object> response = new LinkedHashMap<>();
        response.put("available", false);
        response.put("reason", reason);
        response.put("nodes", List.of());
        response.put("edges", List.of());
        response.put("nextCursor", null);
        response.put("scannedPoints", 0);
        return response;
    }
}
