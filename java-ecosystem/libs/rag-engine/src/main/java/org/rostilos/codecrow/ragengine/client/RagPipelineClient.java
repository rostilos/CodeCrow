package org.rostilos.codecrow.ragengine.client;

import com.fasterxml.jackson.databind.ObjectMapper;
import okhttp3.*;
import org.slf4j.Logger;
import org.slf4j.LoggerFactory;
import org.springframework.beans.factory.annotation.Value;
import org.springframework.stereotype.Service;

import java.io.IOException;
import java.util.HashMap;
import java.util.List;
import java.util.Map;

@Service
public class RagPipelineClient {
    private static final Logger log = LoggerFactory.getLogger(RagPipelineClient.class);
    private static final MediaType JSON = MediaType.parse("application/json; charset=utf-8");

    private final OkHttpClient httpClient;
    private final OkHttpClient longRunningHttpClient;
    private final ObjectMapper objectMapper;
    private final String ragApiUrl;
    private final boolean ragEnabled;
    private final String serviceSecret;

    public RagPipelineClient(
            @Value("${codecrow.rag.api.url:http://rag-pipeline:8001}") String ragApiUrl,
            @Value("${codecrow.rag.api.enabled:true}") boolean ragEnabled,
            @Value("${codecrow.rag.api.timeout.connect:30}") int connectTimeout,
            @Value("${codecrow.rag.api.timeout.read:120}") int readTimeout,
            @Value("${codecrow.rag.api.timeout.indexing:14400}") int indexingTimeout,
            @Value("${codecrow.rag.api.secret:}") String serviceSecret
    ) {
        this.ragApiUrl = ragApiUrl;
        this.ragEnabled = ragEnabled;
        this.serviceSecret = serviceSecret != null ? serviceSecret : "";
        
        this.httpClient = new OkHttpClient.Builder()
                .connectTimeout(connectTimeout, java.util.concurrent.TimeUnit.SECONDS)
                .readTimeout(readTimeout, java.util.concurrent.TimeUnit.SECONDS)
                .writeTimeout(readTimeout, java.util.concurrent.TimeUnit.SECONDS)
                .build();
        
        this.longRunningHttpClient = new OkHttpClient.Builder()
                .connectTimeout(connectTimeout, java.util.concurrent.TimeUnit.SECONDS)
                .readTimeout(indexingTimeout, java.util.concurrent.TimeUnit.SECONDS)
                .writeTimeout(indexingTimeout, java.util.concurrent.TimeUnit.SECONDS)
                .build();
        
        this.objectMapper = new ObjectMapper();
    }

    public Map<String, Object> indexRepository(
            String repoPath,
            String projectWorkspace,
            String projectNamespace,
            String branch,
            String commit,
            List<String> excludePatterns
    ) throws IOException {
        if (!ragEnabled) {
            log.debug("RAG indexing disabled, skipping repository indexing");
            return Map.of("status", "skipped", "reason", "RAG disabled");
        }

        Map<String, Object> payload = new HashMap<>();
        payload.put("repo_path", repoPath);
        payload.put("workspace", projectWorkspace);
        payload.put("project", projectNamespace);
        payload.put("branch", branch);
        payload.put("commit", commit);
        if (excludePatterns != null && !excludePatterns.isEmpty()) {
            payload.put("exclude_patterns", excludePatterns);
        }

        String url = ragApiUrl + "/index/repository";
        return postLongRunning(url, payload);
    }

    public Map<String, Object> updateFiles(
            List<String> filePaths,
            String repoBase,
            String workspace,
            String project,
            String branch,
            String commit
    ) throws IOException {
        if (!ragEnabled) {
            log.debug("RAG indexing disabled, skipping file update");
            return Map.of("status", "skipped", "reason", "RAG disabled");
        }

        Map<String, Object> payload = new HashMap<>();
        payload.put("file_paths", filePaths);
        payload.put("repo_base", repoBase);
        payload.put("workspace", workspace);
        payload.put("project", project);
        payload.put("branch", branch);
        payload.put("commit", commit);

        String url = ragApiUrl + "/index/update-files";
        return post(url, payload);
    }

    public Map<String, Object> deleteFiles(
            List<String> filePaths,
            String workspace,
            String project,
            String branch
    ) throws IOException {
        if (!ragEnabled) {
            return Map.of("status", "skipped", "reason", "RAG disabled");
        }

        Map<String, Object> payload = new HashMap<>();
        payload.put("file_paths", filePaths);
        payload.put("workspace", workspace);
        payload.put("project", project);
        payload.put("branch", branch);

        String url = ragApiUrl + "/index/delete-files";
        return post(url, payload);
    }

    public Map<String, Object> getPRContext(
            String workspace,
            String project,
            String branch,
            List<String> changedFiles,
            String prDescription,
            int topK
    ) throws IOException {
        return getPRContext(workspace, project, branch, null, changedFiles, prDescription, topK, null);
    }

    /**
     * Get PR context with multi-branch support.
     *
     * @param workspace      Workspace identifier
     * @param project        Project identifier
     * @param branch         Target branch (PR source)
     * @param baseBranch     Base branch (PR target, e.g., 'main'). If null, auto-detected.
     * @param changedFiles   List of files changed in PR
     * @param prDescription  PR description text
     * @param topK           Number of results to return
     * @param deletedFiles   Files deleted in target branch (excluded from results)
     * @return               Context with relevant code chunks
     */
    public Map<String, Object> getPRContext(
            String workspace,
            String project,
            String branch,
            String baseBranch,
            List<String> changedFiles,
            String prDescription,
            int topK,
            List<String> deletedFiles
    ) throws IOException {
        if (!ragEnabled) {
            return Map.of("context", Map.of("relevant_code", List.of()));
        }

        Map<String, Object> payload = new HashMap<>();
        payload.put("workspace", workspace);
        payload.put("project", project);
        payload.put("branch", branch);
        payload.put("changed_files", changedFiles);
        payload.put("pr_description", prDescription);
        payload.put("top_k", topK);
        
        if (baseBranch != null) {
            payload.put("base_branch", baseBranch);
        }
        if (deletedFiles != null && !deletedFiles.isEmpty()) {
            payload.put("deleted_files", deletedFiles);
        }

        String url = ragApiUrl + "/query/pr-context";
        return post(url, payload);
    }

    public Map<String, Object> semanticSearch(
            String query,
            String workspace,
            String project,
            String branch,
            int topK,
            String filterLanguage
    ) throws IOException {
        if (!ragEnabled) {
            return Map.of("results", List.of());
        }

        Map<String, Object> payload = new HashMap<>();
        payload.put("query", query);
        payload.put("workspace", workspace);
        payload.put("project", project);
        payload.put("branch", branch);
        payload.put("top_k", topK);
        if (filterLanguage != null) {
            payload.put("filter_language", filterLanguage);
        }

        String url = ragApiUrl + "/query/search";
        return post(url, payload);
    }

    public void deleteIndex(String workspace, String project, String branch) throws IOException {
        if (!ragEnabled) {
            return;
        }

        String url = String.format("%s/index/%s/%s/%s", ragApiUrl, workspace, project, branch);
        Request.Builder builder = new Request.Builder()
                .url(url)
                .delete();
        addAuthHeader(builder);
        Request request = builder.build();

        try (Response response = httpClient.newCall(request).execute()) {
            if (!response.isSuccessful()) {
                log.warn("Failed to delete RAG index for {}/{}/{}: {}",
                    workspace, project, branch, response.code());
            }
        }
    }
    
    // ==========================================================================
    // BRANCH OPERATIONS
    // ==========================================================================
    
    /**
     * Delete all indexed data for a specific branch.
     * Does NOT delete the entire collection - only the branch's data.
     * 
     * Python endpoint: DELETE /index/{workspace}/{project}/branch/{branch}
     */
    public boolean deleteBranch(String workspace, String project, String branch) throws IOException {
        if (!ragEnabled) {
            return false;
        }
        
        // URL-encode branch name to handle slashes (e.g., feature/xyz -> feature%2Fxyz)
        String encodedBranch = java.net.URLEncoder.encode(branch, java.nio.charset.StandardCharsets.UTF_8);
        String url = String.format("%s/index/%s/%s/branch/%s", ragApiUrl, workspace, project, encodedBranch);
        
        Request.Builder builder = new Request.Builder()
                .url(url)
                .delete();
        addAuthHeader(builder);
        Request request = builder.build();
        
        try (Response response = httpClient.newCall(request).execute()) {
            if (response.isSuccessful()) {
                log.info("Deleted branch data for {}/{}/{}", workspace, project, branch);
                return true;
            } else {
                log.warn("Failed to delete branch data: {} - {}", response.code(), 
                        response.body() != null ? response.body().string() : "no body");
                return false;
            }
        }
    }
    
    /**
     * Get list of all branches that have indexed data for a project.
     * 
     * Python endpoint: GET /index/{workspace}/{project}/branches
     */
    @SuppressWarnings("unchecked")
    public List<String> getIndexedBranches(String workspace, String project) {
        if (!ragEnabled) {
            return List.of();
        }
        
        try {
            String url = String.format("%s/index/%s/%s/branches", ragApiUrl, workspace, project);
            Request.Builder builder = new Request.Builder()
                    .url(url)
                    .get();
            addAuthHeader(builder);
            Request request = builder.build();
            
            try (Response response = httpClient.newCall(request).execute()) {
                if (response.isSuccessful() && response.body() != null) {
                    Map<String, Object> result = objectMapper.readValue(response.body().string(), Map.class);
                    // Response format: {"branches": [{"branch": "main", "point_count": 100}, ...]}
                    Object branches = result.get("branches");
                    if (branches instanceof List<?> branchList) {
                        return branchList.stream()
                                .filter(b -> b instanceof Map)
                                .map(b -> (String) ((Map<String, Object>) b).get("branch"))
                                .filter(java.util.Objects::nonNull)
                                .toList();
                    }
                }
                return List.of();
            }
        } catch (IOException e) {
            log.warn("Failed to get indexed branches: {}", e.getMessage());
            return List.of();
        }
    }
    
    /**
     * Get branch statistics with point counts for all branches in a project.
     * 
     * Python endpoint: GET /index/{workspace}/{project}/branches
     * Returns: {"branches": [{"branch": "main", "point_count": 100}, ...], "total_branches": N}
     */
    @SuppressWarnings("unchecked")
    public List<Map<String, Object>> getIndexedBranchesWithStats(String workspace, String project) {
        if (!ragEnabled) {
            return List.of();
        }
        
        try {
            String url = String.format("%s/index/%s/%s/branches", ragApiUrl, workspace, project);
            Request.Builder builder = new Request.Builder()
                    .url(url)
                    .get();
            addAuthHeader(builder);
            Request request = builder.build();
            
            try (Response response = httpClient.newCall(request).execute()) {
                if (response.isSuccessful() && response.body() != null) {
                    Map<String, Object> result = objectMapper.readValue(response.body().string(), Map.class);
                    Object branches = result.get("branches");
                    if (branches instanceof List<?> branchList) {
                        return branchList.stream()
                                .filter(b -> b instanceof Map)
                                .map(b -> (Map<String, Object>) b)
                                .toList();
                    }
                }
                return List.of();
            }
        } catch (IOException e) {
            log.warn("Failed to get indexed branches with stats: {}", e.getMessage());
            return List.of();
        }
    }
    
    /**
     * Cleanup stale branches - delete all branches except protected ones.
     * 
     * Python endpoint: POST /index/{workspace}/{project}/cleanup-branches
     * 
     * @param workspace The workspace
     * @param project The project
     * @param protectedBranches Branches to never delete (default: main, master, develop)
     * @param branchesToKeep Additional branches to keep (e.g., active feature branches)
     * @return Map with cleanup results including deleted/failed branches
     */
    @SuppressWarnings("unchecked")
    public Map<String, Object> cleanupStaleBranches(String workspace, String project, 
            List<String> protectedBranches, List<String> branchesToKeep) {
        if (!ragEnabled) {
            return Map.of("status", "disabled", "message", "RAG is not enabled");
        }
        
        try {
            Map<String, Object> payload = new HashMap<>();
            payload.put("workspace", workspace);
            payload.put("project", project);
            payload.put("protected_branches", protectedBranches != null ? protectedBranches : List.of("main", "master", "develop"));
            if (branchesToKeep != null && !branchesToKeep.isEmpty()) {
                payload.put("branches_to_keep", branchesToKeep);
            }
            
            String url = String.format("%s/index/%s/%s/cleanup-branches", ragApiUrl, workspace, project);
            return post(url, payload);
        } catch (IOException e) {
            log.error("Failed to cleanup stale branches: {}", e.getMessage());
            return Map.of("status", "error", "message", e.getMessage());
        }
    }

    public boolean isHealthy() {
        if (!ragEnabled) {
            return false;
        }

        try {
            Request.Builder builder = new Request.Builder()
                    .url(ragApiUrl + "/health")
                    .get();
            addAuthHeader(builder);
            Request request = builder.build();

            try (Response response = httpClient.newCall(request).execute()) {
                return response.isSuccessful();
            }
        } catch (IOException e) {
            log.warn("RAG health check failed: {}", e.getMessage());
            return false;
        }
    }

    private Map<String, Object> post(String url, Map<String, Object> payload) throws IOException {
        return doRequest(url, payload, httpClient);
    }

    private Map<String, Object> postLongRunning(String url, Map<String, Object> payload) throws IOException {
        return doRequest(url, payload, longRunningHttpClient);
    }

    /**
     * Adds the x-service-secret header to the request if a secret is configured.
     */
    private void addAuthHeader(Request.Builder builder) {
        if (!serviceSecret.isEmpty()) {
            builder.addHeader("x-service-secret", serviceSecret);
        }
    }

    @SuppressWarnings("unchecked")
    private Map<String, Object> doRequest(String url, Map<String, Object> payload, OkHttpClient client) throws IOException {
        String json = objectMapper.writeValueAsString(payload);
        RequestBody body = RequestBody.create(json, JSON);

        Request.Builder builder = new Request.Builder()
                .url(url)
                .post(body);
        addAuthHeader(builder);
        Request request = builder.build();

        try (Response response = client.newCall(request).execute()) {
            String responseBody = response.body() != null ? response.body().string() : "{}";

            if (!response.isSuccessful()) {
                log.error("RAG API request failed: {} - {}", response.code(), responseBody);
                throw new IOException("RAG API error: " + response.code());
            }

            return objectMapper.readValue(responseBody, Map.class);
        }
    }
}
