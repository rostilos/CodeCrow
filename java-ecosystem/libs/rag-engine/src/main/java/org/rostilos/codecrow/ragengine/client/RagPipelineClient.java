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

    public RagPipelineClient(
            @Value("${codecrow.rag.api.url:http://rag-pipeline:8001}") String ragApiUrl,
            @Value("${codecrow.rag.api.enabled:false}") boolean ragEnabled,
            @Value("${codecrow.rag.api.timeout.connect:30}") int connectTimeout,
            @Value("${codecrow.rag.api.timeout.read:120}") int readTimeout,
            @Value("${codecrow.rag.api.timeout.indexing:14400}") int indexingTimeout
    ) {
        this.ragApiUrl = ragApiUrl;
        this.ragEnabled = ragEnabled;
        
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
        Request request = new Request.Builder()
                .url(url)
                .delete()
                .build();

        try (Response response = httpClient.newCall(request).execute()) {
            if (!response.isSuccessful()) {
                log.warn("Failed to delete RAG index for {}/{}/{}: {}",
                    workspace, project, branch, response.code());
            }
        }
    }
    
    // ==========================================================================
    // DELTA INDEX OPERATIONS
    // ==========================================================================
    
    /**
     * Create a delta index for a branch.
     */
    public Map<String, Object> createDeltaIndex(
            String workspace,
            String project,
            String deltaBranch,
            String baseBranch,
            String deltaCommit,
            String rawDiff,
            String vcsType
    ) throws IOException {
        if (!ragEnabled) {
            return Map.of("status", "skipped", "reason", "RAG disabled");
        }
        
        Map<String, Object> payload = new HashMap<>();
        payload.put("workspace", workspace);
        payload.put("project", project);
        payload.put("delta_branch", deltaBranch);
        payload.put("base_branch", baseBranch);
        payload.put("delta_commit", deltaCommit);
        payload.put("raw_diff", rawDiff);
        payload.put("vcs_type", vcsType);
        // repo_path is not available when creating from diff - use workspace/project as identifier
        payload.put("repo_path", workspace + "/" + project);
        
        String url = ragApiUrl + "/delta/index";
        return postLongRunning(url, payload);
    }

    public Map<String, Object> updateDeltaIndex(
            String workspace,
            String project,
            String deltaBranch,
            String newCommit,
            String rawDiff,
            String vcsType
    ) throws IOException {
        if (!ragEnabled) {
            return Map.of("status", "skipped", "reason", "RAG disabled");
        }
        
        Map<String, Object> payload = new HashMap<>();
        payload.put("workspace", workspace);
        payload.put("project", project);
        payload.put("delta_branch", deltaBranch);
        payload.put("delta_commit", newCommit);
        payload.put("raw_diff", rawDiff);
        payload.put("vcs_type", vcsType);
        payload.put("repo_path", workspace + "/" + project);
        
        String url = ragApiUrl + "/delta/index";
        return put(url, payload);
    }

    public void deleteDeltaIndex(String workspace, String project, String deltaBranch) throws IOException {
        if (!ragEnabled) {
            return;
        }
        
        String url = String.format("%s/delta/index/%s/%s/%s", ragApiUrl, workspace, project, deltaBranch);
        Request request = new Request.Builder()
                .url(url)
                .delete()
                .build();
        
        try (Response response = httpClient.newCall(request).execute()) {
            if (!response.isSuccessful()) {
                log.warn("Failed to delete delta index for {}/{}/{}: {}",
                        workspace, project, deltaBranch, response.code());
            }
        }
    }

    @SuppressWarnings("unchecked")
    public boolean deltaIndexExists(String workspace, String project, String deltaBranch) {
        if (!ragEnabled) {
            return false;
        }
        
        try {
            String url = String.format("%s/delta/exists/%s/%s/%s", ragApiUrl, workspace, project, deltaBranch);
            Request request = new Request.Builder()
                    .url(url)
                    .get()
                    .build();
            
            try (Response response = httpClient.newCall(request).execute()) {
                if (response.isSuccessful() && response.body() != null) {
                    Map<String, Object> result = objectMapper.readValue(response.body().string(), Map.class);
                    return Boolean.TRUE.equals(result.get("exists"));
                }
                return false;
            }
        } catch (IOException e) {
            log.warn("Failed to check delta index existence: {}", e.getMessage());
            return false;
        }
    }

    public Map<String, Object> getHybridPRContext(
            String workspace,
            String project,
            String baseBranch,
            String targetBranch,
            List<String> changedFiles,
            String prDescription,
            int topK,
            double deltaBoost
    ) throws IOException {
        if (!ragEnabled) {
            return Map.of("context", Map.of("relevant_code", List.of()));
        }
        
        Map<String, Object> payload = new HashMap<>();
        payload.put("workspace", workspace);
        payload.put("project", project);
        payload.put("base_branch", baseBranch);
        payload.put("target_branch", targetBranch);
        payload.put("changed_files", changedFiles);
        payload.put("pr_description", prDescription);
        payload.put("top_k", topK);
        payload.put("delta_boost", deltaBoost);
        
        String url = ragApiUrl + "/query/pr-context-hybrid";
        return post(url, payload);
    }

    @SuppressWarnings("unchecked")
    public Map<String, Object> shouldUseHybrid(String workspace, String project, String targetBranch) {
        if (!ragEnabled) {
            return Map.of("use_hybrid", false, "reason", "RAG disabled");
        }
        
        try {
            String url = String.format("%s/query/should-use-hybrid/%s/%s/%s",
                    ragApiUrl, workspace, project, targetBranch);
            Request request = new Request.Builder()
                    .url(url)
                    .get()
                    .build();
            
            try (Response response = httpClient.newCall(request).execute()) {
                if (response.isSuccessful() && response.body() != null) {
                    return objectMapper.readValue(response.body().string(), Map.class);
                }
                return Map.of("use_hybrid", false, "reason", "API error");
            }
        } catch (IOException e) {
            log.warn("Failed to check hybrid status: {}", e.getMessage());
            return Map.of("use_hybrid", false, "reason", e.getMessage());
        }
    }

    public boolean isHealthy() {
        if (!ragEnabled) {
            return false;
        }

        try {
            Request request = new Request.Builder()
                    .url(ragApiUrl + "/health")
                    .get()
                    .build();

            try (Response response = httpClient.newCall(request).execute()) {
                return response.isSuccessful();
            }
        } catch (IOException e) {
            log.warn("RAG health check failed: {}", e.getMessage());
            return false;
        }
    }

    private Map<String, Object> post(String url, Map<String, Object> payload) throws IOException {
        return doRequest(url, payload, httpClient, "POST");
    }

    private Map<String, Object> postLongRunning(String url, Map<String, Object> payload) throws IOException {
        return doRequest(url, payload, longRunningHttpClient, "POST");
    }
    
    private Map<String, Object> put(String url, Map<String, Object> payload) throws IOException {
        return doRequest(url, payload, httpClient, "PUT");
    }

    @SuppressWarnings("unchecked")
    private Map<String, Object> doRequest(String url, Map<String, Object> payload, OkHttpClient client, String method) throws IOException {
        String json = objectMapper.writeValueAsString(payload);
        RequestBody body = RequestBody.create(json, JSON);

        Request.Builder requestBuilder = new Request.Builder().url(url);
        if ("PUT".equalsIgnoreCase(method)) {
            requestBuilder.put(body);
        } else {
            requestBuilder.post(body);
        }
        Request request = requestBuilder.build();

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
