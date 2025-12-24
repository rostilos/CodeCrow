package org.rostilos.codecrow.platformmcp.service;

import com.fasterxml.jackson.databind.JsonNode;
import com.fasterxml.jackson.databind.ObjectMapper;
import okhttp3.*;
import org.rostilos.codecrow.vcsclient.bitbucket.cloud.actions.GetPullRequestAction;
import org.rostilos.codecrow.vcsclient.bitbucket.cloud.actions.GetPullRequestDiffAction;
import org.slf4j.Logger;
import org.slf4j.LoggerFactory;

import java.io.IOException;
import java.nio.charset.StandardCharsets;
import java.util.Base64;
import java.util.HashMap;
import java.util.Map;

/**
 * Service for VCS operations in Platform MCP.
 * Reuses vcs-client library actions for Bitbucket/GitHub access.
 * 
 * Authentication is provided via JVM system properties:
 * - accessToken: Direct bearer token
 * - oAuthClient + oAuthSecret: OAuth2 client credentials
 * - workspace: Bitbucket workspace slug
 * - repo.slug: Repository slug
 * - pullRequest.id: PR number
 * - vcs.provider: "bitbucket" or "github" (default: bitbucket)
 */
public class VcsService {
    
    private static final Logger log = LoggerFactory.getLogger(VcsService.class);
    private static final ObjectMapper objectMapper = new ObjectMapper();
    
    private static VcsService instance;
    
    private final OkHttpClient authorizedClient;
    private final String workspace;
    private final String repoSlug;
    private final String pullRequestId;
    private final String vcsProvider;
    private final boolean initialized;
    
    private VcsService() {
        this.workspace = System.getProperty("workspace");
        this.repoSlug = System.getProperty("repo.slug");
        this.pullRequestId = System.getProperty("pullRequest.id");
        this.vcsProvider = System.getProperty("vcs.provider", "bitbucket");
        
        // Check if we have VCS credentials
        String accessToken = System.getProperty("accessToken");
        String oAuthClient = System.getProperty("oAuthClient");
        String oAuthSecret = System.getProperty("oAuthSecret");
        
        boolean hasCredentials = (accessToken != null && !accessToken.isEmpty()) 
                || (oAuthClient != null && !oAuthClient.isEmpty() && oAuthSecret != null && !oAuthSecret.isEmpty());
        
        if (!hasCredentials || workspace == null || repoSlug == null) {
            log.warn("VCS credentials not fully configured. VCS operations will be unavailable.");
            log.warn("  workspace={}, repo.slug={}, hasCredentials={}", workspace, repoSlug, hasCredentials);
            this.authorizedClient = null;
            this.initialized = false;
            return;
        }
        
        // Get bearer token
        String bearerToken;
        if (accessToken != null && !accessToken.isEmpty()) {
            log.info("VcsService: Using provided access token for authentication");
            bearerToken = accessToken;
        } else {
            log.info("VcsService: Using OAuth client credentials for authentication");
            bearerToken = negotiateBearerToken(oAuthClient, oAuthSecret);
        }
        
        // Create authorized OkHttpClient
        this.authorizedClient = new OkHttpClient.Builder()
                .addInterceptor(chain -> {
                    Request newRequest = chain.request().newBuilder()
                            .addHeader("Authorization", "Bearer " + bearerToken)
                            .addHeader("Accept", "application/json")
                            .build();
                    return chain.proceed(newRequest);
                })
                .build();
        
        this.initialized = true;
        log.info("VcsService initialized for workspace={}, repo={}, provider={}", workspace, repoSlug, vcsProvider);
    }
    
    /**
     * Negotiate OAuth bearer token using client credentials.
     */
    private String negotiateBearerToken(String clientId, String clientSecret) {
        OkHttpClient client = new OkHttpClient();
        
        Request request = new Request.Builder()
                .header("Authorization", "Basic " + Base64.getEncoder()
                        .encodeToString((clientId + ":" + clientSecret).getBytes(StandardCharsets.UTF_8)))
                .header("Accept", "application/json")
                .url("https://bitbucket.org/site/oauth2/access_token")
                .post(new FormBody.Builder()
                        .add("grant_type", "client_credentials")
                        .build())
                .build();
        
        try (Response response = client.newCall(request).execute()) {
            if (!response.isSuccessful()) {
                String body = response.body() != null ? response.body().string() : "";
                throw new IllegalStateException("Failed to authenticate: " + response.code() + " - " + body);
            }
            
            String body = response.body() != null ? response.body().string() : "";
            JsonNode node = objectMapper.readTree(body);
            return node.get("access_token").asText();
        } catch (IOException ex) {
            throw new IllegalStateException("Could not retrieve bearer token", ex);
        }
    }
    
    public static synchronized VcsService getInstance() {
        if (instance == null) {
            instance = new VcsService();
        }
        return instance;
    }
    
    public boolean isInitialized() {
        return initialized;
    }
    
    /**
     * Get PR metadata (title, description, state, branches).
     */
    public Map<String, Object> getPullRequestData(Long projectId, Integer prId) throws IOException {
        if (!initialized) {
            throw new IOException("VCS service not initialized - missing credentials");
        }
        
        String prNumber = prId != null ? String.valueOf(prId) : pullRequestId;
        if (prNumber == null) {
            throw new IllegalArgumentException("PR number not provided");
        }
        
        if ("bitbucket".equalsIgnoreCase(vcsProvider)) {
            return getBitbucketPullRequest(prNumber);
        } else if ("github".equalsIgnoreCase(vcsProvider)) {
            return getGithubPullRequest(prNumber);
        } else {
            throw new IOException("Unsupported VCS provider: " + vcsProvider);
        }
    }
    
    /**
     * Get PR diff.
     */
    public Map<String, Object> getPullRequestDiff(Long projectId, Integer prId, String filePath, Boolean contextOnly) throws IOException {
        if (!initialized) {
            throw new IOException("VCS service not initialized - missing credentials");
        }
        
        String prNumber = prId != null ? String.valueOf(prId) : pullRequestId;
        if (prNumber == null) {
            throw new IllegalArgumentException("PR number not provided");
        }
        
        if ("bitbucket".equalsIgnoreCase(vcsProvider)) {
            return getBitbucketPullRequestDiff(prNumber, filePath, contextOnly);
        } else if ("github".equalsIgnoreCase(vcsProvider)) {
            return getGithubPullRequestDiff(prNumber, filePath, contextOnly);
        } else {
            throw new IOException("Unsupported VCS provider: " + vcsProvider);
        }
    }
    
    private Map<String, Object> getBitbucketPullRequest(String prNumber) throws IOException {
        GetPullRequestAction action = new GetPullRequestAction(authorizedClient);
        GetPullRequestAction.PullRequestMetadata metadata = action.getPullRequest(workspace, repoSlug, prNumber);
        
        Map<String, Object> result = new HashMap<>();
        result.put("id", Integer.parseInt(prNumber));
        result.put("title", metadata.getTitle());
        result.put("description", metadata.getDescription());
        result.put("state", metadata.getState());
        result.put("sourceBranch", metadata.getSourceRef());
        result.put("targetBranch", metadata.getDestRef());
        result.put("workspace", workspace);
        result.put("repository", repoSlug);
        result.put("provider", "bitbucket");
        
        return result;
    }
    
    private Map<String, Object> getBitbucketPullRequestDiff(String prNumber, String filePath, Boolean contextOnly) throws IOException {
        GetPullRequestDiffAction action = new GetPullRequestDiffAction(authorizedClient);
        String rawDiff = action.getPullRequestDiff(workspace, repoSlug, prNumber);
        
        Map<String, Object> result = new HashMap<>();
        result.put("prId", Integer.parseInt(prNumber));
        result.put("workspace", workspace);
        result.put("repository", repoSlug);
        result.put("provider", "bitbucket");
        
        // Filter by file path if requested
        if (filePath != null && !filePath.isEmpty()) {
            String filteredDiff = filterDiffByFile(rawDiff, filePath);
            result.put("diffContent", filteredDiff);
            result.put("filteredByFile", filePath);
        } else {
            result.put("diffContent", rawDiff);
        }
        
        // Parse diff statistics
        DiffStats stats = parseDiffStats(rawDiff);
        result.put("additions", stats.additions);
        result.put("deletions", stats.deletions);
        result.put("changedFiles", stats.changedFiles);
        
        return result;
    }
    
    private Map<String, Object> getGithubPullRequest(String prNumber) throws IOException {
        // GitHub implementation using vcs-client's GitHub actions
        org.rostilos.codecrow.vcsclient.github.actions.GetPullRequestAction action = 
                new org.rostilos.codecrow.vcsclient.github.actions.GetPullRequestAction(authorizedClient);
        
        JsonNode prJson = action.getPullRequest(workspace, repoSlug, Integer.parseInt(prNumber));
        
        Map<String, Object> result = new HashMap<>();
        result.put("id", Integer.parseInt(prNumber));
        result.put("title", prJson.has("title") ? prJson.get("title").asText() : "");
        result.put("description", prJson.has("body") ? prJson.get("body").asText() : "");
        result.put("state", prJson.has("state") ? prJson.get("state").asText() : "");
        result.put("sourceBranch", prJson.has("head") && prJson.get("head").has("ref") 
                ? prJson.get("head").get("ref").asText() : "");
        result.put("targetBranch", prJson.has("base") && prJson.get("base").has("ref") 
                ? prJson.get("base").get("ref").asText() : "");
        result.put("owner", workspace);
        result.put("repository", repoSlug);
        result.put("provider", "github");
        
        return result;
    }
    
    private Map<String, Object> getGithubPullRequestDiff(String prNumber, String filePath, Boolean contextOnly) throws IOException {
        org.rostilos.codecrow.vcsclient.github.actions.GetPullRequestDiffAction action = 
                new org.rostilos.codecrow.vcsclient.github.actions.GetPullRequestDiffAction(authorizedClient);
        String rawDiff = action.getPullRequestDiff(workspace, repoSlug, Integer.parseInt(prNumber));
        
        Map<String, Object> result = new HashMap<>();
        result.put("prId", Integer.parseInt(prNumber));
        result.put("owner", workspace);
        result.put("repository", repoSlug);
        result.put("provider", "github");
        
        if (filePath != null && !filePath.isEmpty()) {
            String filteredDiff = filterDiffByFile(rawDiff, filePath);
            result.put("diffContent", filteredDiff);
            result.put("filteredByFile", filePath);
        } else {
            result.put("diffContent", rawDiff);
        }
        
        DiffStats stats = parseDiffStats(rawDiff);
        result.put("additions", stats.additions);
        result.put("deletions", stats.deletions);
        result.put("changedFiles", stats.changedFiles);
        
        return result;
    }
    
    /**
     * Filter diff to only include changes for a specific file.
     */
    private String filterDiffByFile(String fullDiff, String filePath) {
        StringBuilder filtered = new StringBuilder();
        String[] lines = fullDiff.split("\n");
        boolean inTargetFile = false;
        
        for (String line : lines) {
            if (line.startsWith("diff --git")) {
                inTargetFile = line.contains(filePath);
            }
            if (inTargetFile) {
                filtered.append(line).append("\n");
            }
        }
        
        return filtered.toString();
    }
    
    /**
     * Parse diff to extract statistics.
     */
    private DiffStats parseDiffStats(String diff) {
        int additions = 0;
        int deletions = 0;
        int changedFiles = 0;
        
        for (String line : diff.split("\n")) {
            if (line.startsWith("diff --git")) {
                changedFiles++;
            } else if (line.startsWith("+") && !line.startsWith("+++")) {
                additions++;
            } else if (line.startsWith("-") && !line.startsWith("---")) {
                deletions++;
            }
        }
        
        return new DiffStats(additions, deletions, changedFiles);
    }
    
    private static class DiffStats {
        final int additions;
        final int deletions;
        final int changedFiles;
        
        DiffStats(int additions, int deletions, int changedFiles) {
            this.additions = additions;
            this.deletions = deletions;
            this.changedFiles = changedFiles;
        }
    }
}
