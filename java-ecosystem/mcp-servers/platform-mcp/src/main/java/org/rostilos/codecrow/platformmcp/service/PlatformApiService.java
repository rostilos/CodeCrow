package org.rostilos.codecrow.platformmcp.service;

import com.fasterxml.jackson.core.type.TypeReference;
import com.fasterxml.jackson.databind.ObjectMapper;
import okhttp3.OkHttpClient;
import okhttp3.Request;
import okhttp3.Response;
import org.slf4j.Logger;
import org.slf4j.LoggerFactory;

import java.io.IOException;
import java.net.URLEncoder;
import java.nio.charset.StandardCharsets;
import java.util.List;
import java.util.Map;
import java.util.concurrent.TimeUnit;

/**
 * Service for accessing CodeCrow platform API.
 * Uses REST API instead of direct database access.
 * 
 * Security:
 * - All requests include X-Internal-Secret header
 * - All requests include projectId for data isolation
 * - projectId originates from validated webhook chain
 */
public class PlatformApiService {
    
    private static final Logger log = LoggerFactory.getLogger(PlatformApiService.class);
    private static final ObjectMapper objectMapper = new ObjectMapper();
    private static final String INTERNAL_SECRET_HEADER = "X-Internal-Secret";
    
    private final String apiBaseUrl;
    private final Long projectId;
    private final String internalSecret;
    private final OkHttpClient httpClient;
    
    private static PlatformApiService instance;
    
    public PlatformApiService(String apiBaseUrl, Long projectId, String internalSecret) {
        this.apiBaseUrl = apiBaseUrl;
        this.projectId = projectId;
        this.internalSecret = internalSecret;
        this.httpClient = new OkHttpClient.Builder()
                .connectTimeout(30, TimeUnit.SECONDS)
                .readTimeout(30, TimeUnit.SECONDS)
                .build();
        log.info("PlatformApiService initialized with API URL: {}, projectId: {}", apiBaseUrl, projectId);
    }
    
    public static synchronized PlatformApiService getInstance() {
        if (instance == null) {
            String url = System.getProperty("api.base.url", "http://localhost:8081");
            String projectIdStr = System.getProperty("project.id", "0");
            String secret = System.getProperty("internal.api.secret", "");
            Long projectId = Long.parseLong(projectIdStr);
            instance = new PlatformApiService(url, projectId, secret);
        }
        return instance;
    }
    
    public Long getProjectId() {
        return projectId;
    }
    
    /**
     * Get details of a specific issue by ID.
     * Security: projectId is always included to ensure data isolation.
     */
    public Map<String, Object> getIssueDetails(Long issueId) throws IOException {
        // Include projectId for security - API will verify issue belongs to this project
        String url = apiBaseUrl + "/api/internal/issues/" + issueId + "?projectId=" + projectId;
        
        Request.Builder requestBuilder = new Request.Builder()
                .url(url)
                .get();
        
        if (internalSecret != null && !internalSecret.isBlank()) {
            requestBuilder.addHeader(INTERNAL_SECRET_HEADER, internalSecret);
        }
        
        Request request = requestBuilder.build();
        
        log.info("Fetching issue details from: {}", url);
        
        try (Response response = httpClient.newCall(request).execute()) {
            String body = response.body() != null ? response.body().string() : "";
            
            if (response.isSuccessful()) {
                return objectMapper.readValue(body, new TypeReference<Map<String, Object>>() {});
            } else if (response.code() == 404) {
                log.warn("Issue not found: {}", issueId);
                return null;
            } else if (response.code() == 403) {
                log.warn("Issue {} does not belong to project {}", issueId, projectId);
                return null;
            } else {
                log.error("API error fetching issue {}: {} - {}", issueId, response.code(), body);
                throw new IOException("API error: " + response.code() + " - " + body);
            }
        }
    }
    
    /**
     * Search issues by criteria.
     * Security: Uses the projectId from constructor (from validated webhook chain).
     */
    public List<Map<String, Object>> searchIssues(String severity, String category, 
                                                   String status, Integer limit) throws IOException {
        StringBuilder urlBuilder = new StringBuilder(apiBaseUrl)
                .append("/api/internal/issues?projectId=")
                .append(projectId);
        
        if (severity != null && !severity.isEmpty()) {
            urlBuilder.append("&severity=").append(URLEncoder.encode(severity, StandardCharsets.UTF_8));
        }
        if (category != null && !category.isEmpty()) {
            urlBuilder.append("&category=").append(URLEncoder.encode(category, StandardCharsets.UTF_8));
        }
        if (status != null && !status.isEmpty()) {
            urlBuilder.append("&status=").append(URLEncoder.encode(status, StandardCharsets.UTF_8));
        }
        urlBuilder.append("&limit=").append(limit != null ? limit : 50);
        
        String url = urlBuilder.toString();
        
        Request.Builder requestBuilder = new Request.Builder()
                .url(url)
                .get();
        
        if (internalSecret != null && !internalSecret.isBlank()) {
            requestBuilder.addHeader(INTERNAL_SECRET_HEADER, internalSecret);
        }
        
        Request request = requestBuilder.build();
        
        log.info("Searching issues from: {}", url);
        
        try (Response response = httpClient.newCall(request).execute()) {
            String body = response.body() != null ? response.body().string() : "";
            
            if (response.isSuccessful()) {
                return objectMapper.readValue(body, new TypeReference<List<Map<String, Object>>>() {});
            } else {
                log.error("API error searching issues: {} - {}", response.code(), body);
                throw new IOException("API error: " + response.code() + " - " + body);
            }
        }
    }
    
    /**
     * Get analysis results for a project/PR.
     */
    public Map<String, Object> getAnalysisResults(Long pullRequestId) throws IOException {
        StringBuilder urlBuilder = new StringBuilder(apiBaseUrl)
                .append("/api/internal/analysis?projectId=")
                .append(projectId);
        
        if (pullRequestId != null) {
            urlBuilder.append("&pullRequestId=").append(pullRequestId);
        }
        
        String url = urlBuilder.toString();
        
        Request.Builder requestBuilder = new Request.Builder()
                .url(url)
                .get();
        
        if (internalSecret != null && !internalSecret.isBlank()) {
            requestBuilder.addHeader(INTERNAL_SECRET_HEADER, internalSecret);
        }
        
        Request request = requestBuilder.build();
        
        log.info("Fetching analysis results from: {}", url);
        
        try (Response response = httpClient.newCall(request).execute()) {
            String body = response.body() != null ? response.body().string() : "";
            
            if (response.isSuccessful()) {
                return objectMapper.readValue(body, new TypeReference<Map<String, Object>>() {});
            } else if (response.code() == 404) {
                return null;
            } else {
                log.error("API error fetching analysis: {} - {}", response.code(), body);
                throw new IOException("API error: " + response.code() + " - " + body);
            }
        }
    }
}
