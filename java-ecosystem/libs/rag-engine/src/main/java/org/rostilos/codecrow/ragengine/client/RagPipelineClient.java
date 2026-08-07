package org.rostilos.codecrow.ragengine.client;

import com.fasterxml.jackson.databind.ObjectMapper;
import okhttp3.*;
import org.slf4j.Logger;
import org.slf4j.LoggerFactory;
import org.rostilos.codecrow.ragengine.source.RepositorySourceTreeIdentity;
import org.springframework.beans.factory.annotation.Value;
import org.springframework.stereotype.Service;

import java.io.IOException;
import java.nio.file.Path;
import java.util.HashMap;
import java.util.LinkedHashMap;
import java.util.List;
import java.util.Map;
import java.util.function.Consumer;

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
        this.ragApiUrl = normalizeBaseUrl(ragApiUrl);
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

    private static String normalizeBaseUrl(String baseUrl) {
        if (baseUrl == null || baseUrl.isEmpty()) {
            return "";
        }
        int end = baseUrl.length();
        while (end > 0 && baseUrl.charAt(end - 1) == '/') {
            end--;
        }
        return end == baseUrl.length() ? baseUrl : baseUrl.substring(0, end);
    }

    public Map<String, Object> indexRepository(
            String repoPath,
            String projectWorkspace,
            String projectNamespace,
            String branch,
            String commit,
            List<String> includePatterns,
            List<String> excludePatterns
    ) throws IOException {
        return indexRepository(
                repoPath, projectWorkspace, projectNamespace, branch, commit,
                includePatterns, excludePatterns, null);
    }

    public Map<String, Object> indexRepository(
            String repoPath,
            String projectWorkspace,
            String projectNamespace,
            String branch,
            String commit,
            List<String> includePatterns,
            List<String> excludePatterns,
            String collectionTarget
    ) throws IOException {
        return indexRepository(
                repoPath, projectWorkspace, projectNamespace, branch, commit,
                includePatterns, excludePatterns, collectionTarget, false, false);
    }

    /**
     * Index an immutable generation and optionally publish its readable branch
     * and legacy-project aliases in the same Qdrant transaction.
     */
    public Map<String, Object> indexRepository(
            String repoPath,
            String projectWorkspace,
            String projectNamespace,
            String branch,
            String commit,
            List<String> includePatterns,
            List<String> excludePatterns,
            String collectionTarget,
            boolean publishBranchAlias,
            boolean publishLegacyProjectAlias
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
        if (collectionTarget != null && !collectionTarget.isBlank()) {
            payload.put("collection_target", collectionTarget);
        }
        if (publishBranchAlias) {
            payload.put("publish_branch_alias", true);
        }
        if (publishLegacyProjectAlias) {
            payload.put("publish_legacy_project_alias", true);
        }
        payload.put(
                "source_tree_sha256",
                RepositorySourceTreeIdentity.sha256(Path.of(repoPath))
        );
        if (includePatterns != null && !includePatterns.isEmpty()) {
            payload.put("include_patterns", includePatterns);
        }
        if (excludePatterns != null && !excludePatterns.isEmpty()) {
            payload.put("exclude_patterns", excludePatterns);
        }

        String url = ragApiUrl + "/index/repository";
        return postLongRunning(url, payload);
    }

    /**
     * Build an index through the progress-streaming transport.  The regular
     * JSON method above remains available for legacy callers; this overload is
     * used by explicit branch maintenance so detailed batch events can reach
     * the operator without affecting index correctness.
     */
    public Map<String, Object> indexRepository(
            String repoPath,
            String projectWorkspace,
            String projectNamespace,
            String branch,
            String commit,
            List<String> includePatterns,
            List<String> excludePatterns,
            String collectionTarget,
            Consumer<Map<String, Object>> progressConsumer
    ) throws IOException {
        return indexRepository(
                repoPath, projectWorkspace, projectNamespace, branch, commit,
                includePatterns, excludePatterns, collectionTarget, false, false,
                progressConsumer);
    }

    /** Streaming variant of exact generation indexing with alias publication. */
    public Map<String, Object> indexRepository(
            String repoPath,
            String projectWorkspace,
            String projectNamespace,
            String branch,
            String commit,
            List<String> includePatterns,
            List<String> excludePatterns,
            String collectionTarget,
            boolean publishBranchAlias,
            boolean publishLegacyProjectAlias,
            Consumer<Map<String, Object>> progressConsumer
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
        if (collectionTarget != null && !collectionTarget.isBlank()) {
            payload.put("collection_target", collectionTarget);
        }
        if (publishBranchAlias) {
            payload.put("publish_branch_alias", true);
        }
        if (publishLegacyProjectAlias) {
            payload.put("publish_legacy_project_alias", true);
        }
        payload.put("source_tree_sha256", RepositorySourceTreeIdentity.sha256(Path.of(repoPath)));
        if (includePatterns != null && !includePatterns.isEmpty()) {
            payload.put("include_patterns", includePatterns);
        }
        if (excludePatterns != null && !excludePatterns.isEmpty()) {
            payload.put("exclude_patterns", excludePatterns);
        }
        return postLongRunningSse(
                ragApiUrl + "/index/repository/stream", payload, progressConsumer);
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
        return postLongRunning(url, payload);
    }

    public Map<String, Object> deleteFiles(
            List<String> filePaths,
            String workspace,
            String project,
            String branch
    ) throws IOException {
        return deleteFiles(filePaths, workspace, project, branch, null);
    }

    public Map<String, Object> deleteFiles(
            List<String> filePaths,
            String workspace,
            String project,
            String branch,
            String commit
    ) throws IOException {
        if (!ragEnabled) {
            return Map.of("status", "skipped", "reason", "RAG disabled");
        }

        Map<String, Object> payload = new HashMap<>();
        payload.put("file_paths", filePaths);
        payload.put("workspace", workspace);
        payload.put("project", project);
        payload.put("branch", branch);
        if (commit != null && !commit.isBlank()) {
            payload.put("commit", commit);
        }

        String url = ragApiUrl + "/index/delete-files";
        return postLongRunning(url, payload);
    }

    public Map<String, Object> applyChanges(
            List<String> updatedFilePaths,
            List<String> deletedFilePaths,
            String repoBase,
            String workspace,
            String project,
            String branch,
            String commit
    ) throws IOException {
        if (!ragEnabled) {
            log.debug("RAG indexing disabled, skipping incremental change set");
            return Map.of("status", "skipped", "reason", "RAG disabled");
        }

        Map<String, Object> payload = new HashMap<>();
        payload.put("updated_file_paths", updatedFilePaths);
        payload.put("deleted_file_paths", deletedFilePaths);
        if (repoBase != null && !repoBase.isBlank()) {
            payload.put("repo_base", repoBase);
        }
        payload.put("workspace", workspace);
        payload.put("project", project);
        payload.put("branch", branch);
        payload.put("commit", commit);

        return postLongRunning(ragApiUrl + "/index/apply-changes", payload);
    }

    public Map<String, Object> advanceGeneration(
            List<String> updatedFilePaths,
            List<String> deletedFilePaths,
            String repoBase,
            String workspace,
            String project,
            String branch,
            String sourceCommit,
            String commit,
            String sourceTreeSha256,
            String sourceCollectionTarget,
            String collectionTarget
    ) throws IOException {
        return advanceGeneration(
                updatedFilePaths, deletedFilePaths, repoBase, workspace, project,
                branch, sourceCommit, commit, sourceTreeSha256,
                sourceCollectionTarget, collectionTarget, false, false);
    }

    /** Advance an exact generation and atomically update its readable aliases. */
    public Map<String, Object> advanceGeneration(
            List<String> updatedFilePaths,
            List<String> deletedFilePaths,
            String repoBase,
            String workspace,
            String project,
            String branch,
            String sourceCommit,
            String commit,
            String sourceTreeSha256,
            String sourceCollectionTarget,
            String collectionTarget,
            boolean publishBranchAlias,
            boolean publishLegacyProjectAlias
    ) throws IOException {
        if (!ragEnabled) {
            log.debug("RAG indexing disabled, skipping generation advance");
            return Map.of("status", "skipped", "reason", "RAG disabled");
        }

        Map<String, Object> payload = new HashMap<>();
        payload.put("updated_file_paths", updatedFilePaths);
        payload.put("deleted_file_paths", deletedFilePaths);
        if (repoBase != null && !repoBase.isBlank()) {
            payload.put("repo_base", repoBase);
        }
        payload.put("workspace", workspace);
        payload.put("project", project);
        payload.put("branch", branch);
        payload.put("source_commit", sourceCommit);
        payload.put("commit", commit);
        payload.put("source_tree_sha256", sourceTreeSha256);
        payload.put("source_collection_target", sourceCollectionTarget);
        payload.put("collection_target", collectionTarget);
        if (publishBranchAlias) {
            payload.put("publish_branch_alias", true);
        }
        if (publishLegacyProjectAlias) {
            payload.put("publish_legacy_project_alias", true);
        }

        return postLongRunning(ragApiUrl + "/index/advance-generation", payload);
    }

    /**
     * Idempotently repair human-readable aliases of one completed generation.
     * Exact analysis never depends on this convenience mapping.
     */
    public void publishGenerationAliases(
            String workspace,
            String project,
            String branch,
            String commit,
            String collectionTarget,
            boolean publishBranchAlias,
            boolean publishLegacyProjectAlias) throws IOException {
        if (!ragEnabled || !publishBranchAlias) {
            return;
        }
        Map<String, Object> payload = new HashMap<>();
        payload.put("workspace", workspace);
        payload.put("project", project);
        payload.put("branch", branch);
        payload.put("commit", commit);
        payload.put("collection_target", collectionTarget);
        payload.put("publish_branch_alias", true);
        if (publishLegacyProjectAlias) {
            payload.put("publish_legacy_project_alias", true);
        }
        post(ragApiUrl + "/index/generation-aliases", payload);
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
    // PR-SPECIFIC OPERATIONS
    // ==========================================================================

    /**
     * Delete all indexed points for a specific PR from the project's collection.
     * Called after PR analysis completes or when a PR is closed/merged to clean up
     * PR-specific data from Qdrant.
     * 
     * This operation is idempotent — calling it for a PR with no indexed points
     * returns status "skipped".
     * 
     * Python endpoint: DELETE /index/pr-files/{workspace}/{project}/{pr_number}
     * 
     * @param workspace  Workspace identifier
     * @param project    Project identifier
     * @param prNumber   PR number whose indexed points should be deleted
     * @return true if points were deleted or already absent, false on error
     */
    public boolean deletePrFiles(String workspace, String project, int prNumber) {
        if (!ragEnabled) {
            log.debug("RAG disabled, skipping PR files deletion");
            return true;
        }

        String url = String.format("%s/index/pr-files/%s/%s/%d", ragApiUrl, workspace, project, prNumber);

        Request.Builder builder = new Request.Builder()
                .url(url)
                .delete();
        addAuthHeader(builder);
        Request request = builder.build();

        try (Response response = httpClient.newCall(request).execute()) {
            if (response.isSuccessful()) {
                log.info("Deleted PR #{} indexed data from {}/{}", prNumber, workspace, project);
                return true;
            } else {
                log.warn("Failed to delete PR #{} files: {} - {}", prNumber, response.code(),
                        response.body() != null ? response.body().string() : "no body");
                return false;
            }
        } catch (IOException e) {
            log.warn("Error deleting PR #{} files from {}/{}: {}", prNumber, workspace, project, e.getMessage());
            return false;
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
        return deleteBranch(workspace, project, branch, null);
    }

    public boolean deleteBranch(
            String workspace,
            String project,
            String branch,
            String collectionTarget
    ) throws IOException {
        if (!ragEnabled) {
            return false;
        }
        
        // URL-encode branch name to handle slashes (e.g., feature/xyz -> feature%2Fxyz)
        String encodedBranch = java.net.URLEncoder.encode(branch, java.nio.charset.StandardCharsets.UTF_8);
        HttpUrl.Builder urlBuilder = HttpUrl.get(String.format(
                "%s/index/%s/%s/branch/%s", ragApiUrl, workspace, project, encodedBranch)).newBuilder();
        if (collectionTarget != null && !collectionTarget.isBlank()) {
            urlBuilder.addQueryParameter("collection_target", collectionTarget);
        }
        
        Request.Builder builder = new Request.Builder()
                .url(urlBuilder.build())
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
     * @param protectedBranches Explicit non-empty set of authoritative branches to
     *                          never delete
     * @param branchesToKeep Additional branches to keep (e.g., active feature branches)
     * @return Map with cleanup results including deleted/failed branches
     */
    @SuppressWarnings("unchecked")
    public Map<String, Object> cleanupStaleBranches(String workspace, String project, 
            List<String> protectedBranches, List<String> branchesToKeep) {
        if (!ragEnabled) {
            return Map.of("status", "disabled", "message", "RAG is not enabled");
        }
        if (protectedBranches == null || protectedBranches.isEmpty()) {
            throw new IllegalArgumentException(
                    "protectedBranches must contain the authoritative repository branch");
        }
        validateExactBranchIdentities("protectedBranches", protectedBranches);
        if (branchesToKeep != null) {
            validateExactBranchIdentities("branchesToKeep", branchesToKeep);
        }
        
        try {
            Map<String, Object> payload = new HashMap<>();
            payload.put("workspace", workspace);
            payload.put("project", project);
            payload.put("protected_branches", protectedBranches);
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

    private static void validateExactBranchIdentities(String fieldName, List<String> branches) {
        if (branches.stream().anyMatch(branch -> branch == null
                || branch.isBlank()
                || !branch.equals(branch.trim()))) {
            throw new IllegalArgumentException(
                    fieldName + " must contain non-blank exact repository branch identities");
        }
        if (branches.stream().distinct().count() != branches.size()) {
            throw new IllegalArgumentException(fieldName + " must contain unique branch identities");
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

    @SuppressWarnings("unchecked")
    private Map<String, Object> postLongRunningSse(
            String url,
            Map<String, Object> payload,
            Consumer<Map<String, Object>> progressConsumer
    ) throws IOException {
        RequestBody body = RequestBody.create(objectMapper.writeValueAsString(payload), JSON);
        Request.Builder builder = new Request.Builder()
                .url(url)
                .header("Accept", "text/event-stream")
                .post(body);
        addAuthHeader(builder);

        try (Response response = longRunningHttpClient.newCall(builder.build()).execute()) {
            if (!response.isSuccessful()) {
                String detail = response.body() != null ? response.body().string() : "{}";
                throw new IOException("RAG API error: " + response.code() + " — " + detail);
            }
            if (response.body() == null) {
                throw new IOException("RAG progress stream returned no body");
            }
            String line;
            while ((line = response.body().source().readUtf8Line()) != null) {
                if (!line.startsWith("data:")) {
                    continue;
                }
                String json = line.substring(5).trim();
                if (json.isEmpty()) {
                    continue;
                }
                Map<String, Object> event = objectMapper.readValue(json, Map.class);
                String type = String.valueOf(event.get("type"));
                if ("progress".equals(type)) {
                    if (progressConsumer != null) {
                        progressConsumer.accept(new LinkedHashMap<>(event));
                    }
                    continue;
                }
                if ("complete".equals(type)) {
                    Object result = event.get("result");
                    if (result instanceof Map<?, ?> resultMap) {
                        return new LinkedHashMap<>((Map<String, Object>) resultMap);
                    }
                    throw new IOException("RAG progress stream completed without index result");
                }
                if ("error".equals(type)) {
                    throw new IOException("RAG API error: " + event.getOrDefault("message", "unknown error"));
                }
            }
        }
        throw new IOException("RAG progress stream ended without a terminal result");
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
                // Include truncated response body in exception so callers can see the actual error
                String detail = responseBody.length() > 500
                        ? responseBody.substring(0, 500) + "..."
                        : responseBody;
                throw new IOException("RAG API error: " + response.code() + " — " + detail);
            }

            return objectMapper.readValue(responseBody, Map.class);
        }
    }
}
