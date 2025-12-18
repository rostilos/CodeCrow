package org.rostilos.codecrow.platformmcp.client;

import com.fasterxml.jackson.databind.JsonNode;
import com.fasterxml.jackson.databind.ObjectMapper;
import okhttp3.OkHttpClient;
import okhttp3.Request;
import okhttp3.Response;
import org.slf4j.Logger;
import org.slf4j.LoggerFactory;

import java.io.IOException;
import java.net.URLEncoder;
import java.nio.charset.StandardCharsets;
import java.util.concurrent.TimeUnit;

/**
 * HTTP client for calling CodeCrow internal API.
 * Similar to how BitbucketCloudClient works for Bitbucket MCP.
 */
public class CodeCrowApiClient {
    
    private static final Logger log = LoggerFactory.getLogger(CodeCrowApiClient.class);
    private static final ObjectMapper objectMapper = new ObjectMapper();
    private static final String INTERNAL_SECRET_HEADER = "X-Internal-Secret";
    
    private final OkHttpClient httpClient;
    private final String baseUrl;
    private final String internalSecret;
    
    public CodeCrowApiClient(String baseUrl, String internalSecret) {
        this.baseUrl = baseUrl;
        this.internalSecret = internalSecret;
        this.httpClient = new OkHttpClient.Builder()
                .connectTimeout(10, TimeUnit.SECONDS)
                .readTimeout(30, TimeUnit.SECONDS)
                .build();
        log.info("CodeCrowApiClient initialized with baseUrl: {}", baseUrl);
    }
    
    /**
     * Get issue details by ID.
     */
    public JsonNode getIssueById(Long issueId) throws IOException {
        String url = baseUrl + "/api/internal/issues/" + issueId;
        log.debug("Fetching issue from: {}", url);
        
        Request.Builder requestBuilder = new Request.Builder()
                .url(url)
                .get();
        
        if (internalSecret != null && !internalSecret.isBlank()) {
            requestBuilder.addHeader(INTERNAL_SECRET_HEADER, internalSecret);
        }
        
        Request request = requestBuilder.build();
        
        try (Response response = httpClient.newCall(request).execute()) {
            String body = response.body() != null ? response.body().string() : "";
            
            if (response.isSuccessful()) {
                return objectMapper.readTree(body);
            } else if (response.code() == 404) {
                log.debug("Issue {} not found", issueId);
                return null;
            } else {
                log.error("Failed to fetch issue {}: {} - {}", issueId, response.code(), body);
                throw new IOException("API error: " + response.code());
            }
        }
    }
    
    /**
     * Search issues with filters.
     */
    public JsonNode searchIssues(Long projectId, String severity, String category, 
                                  String branch, String pullRequestId, Integer limit) throws IOException {
        StringBuilder urlBuilder = new StringBuilder(baseUrl)
                .append("/api/internal/issues?projectId=")
                .append(projectId);
        
        if (severity != null && !severity.isEmpty()) {
            urlBuilder.append("&severity=").append(URLEncoder.encode(severity, StandardCharsets.UTF_8));
        }
        if (category != null && !category.isEmpty()) {
            urlBuilder.append("&category=").append(URLEncoder.encode(category, StandardCharsets.UTF_8));
        }
        if (branch != null && !branch.isEmpty()) {
            urlBuilder.append("&branch=").append(URLEncoder.encode(branch, StandardCharsets.UTF_8));
        }
        if (pullRequestId != null && !pullRequestId.isEmpty()) {
            urlBuilder.append("&pullRequestId=").append(URLEncoder.encode(pullRequestId, StandardCharsets.UTF_8));
        }
        if (limit != null && limit > 0) {
            urlBuilder.append("&limit=").append(limit);
        }
        
        String url = urlBuilder.toString();
        log.debug("Searching issues from: {}", url);
        
        Request.Builder requestBuilder = new Request.Builder()
                .url(url)
                .get();
        
        if (internalSecret != null && !internalSecret.isBlank()) {
            requestBuilder.addHeader(INTERNAL_SECRET_HEADER, internalSecret);
        }
        
        Request request = requestBuilder.build();
        
        try (Response response = httpClient.newCall(request).execute()) {
            String body = response.body() != null ? response.body().string() : "";
            
            if (response.isSuccessful()) {
                return objectMapper.readTree(body);
            } else {
                log.error("Failed to search issues: {} - {}", response.code(), body);
                throw new IOException("API error: " + response.code());
            }
        }
    }
}
