package org.rostilos.codecrow.ragengine.client;

import com.fasterxml.jackson.databind.ObjectMapper;
import okhttp3.*;
import org.slf4j.Logger;
import org.slf4j.LoggerFactory;
import org.rostilos.codecrow.ragengine.source.RepositorySourceTreeIdentity;
import org.springframework.beans.factory.annotation.Autowired;
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
    private final OkHttpClient streamingHttpClient;
    private final ObjectMapper objectMapper;
    private final String ragApiUrl;
    private final boolean ragEnabled;
    private final String serviceSecret;

    /** HTTP response failure with enough structure for bounded retry decisions. */
    public static final class RagApiException extends IOException {
        private final int statusCode;

        public RagApiException(int statusCode, String detail) {
            super("RAG API error: " + statusCode + " — " + detail);
            this.statusCode = statusCode;
        }

        public int getStatusCode() {
            return statusCode;
        }

        public boolean isServiceFailure() {
            return statusCode == 401
                    || statusCode == 403
                    || statusCode == 408
                    || statusCode == 429
                    || statusCode >= 500;
        }
    }

    public RagPipelineClient(
            String ragApiUrl,
            boolean ragEnabled,
            int connectTimeout,
            int readTimeout,
            int indexingTimeout,
            String serviceSecret
    ) {
        this(
                ragApiUrl,
                ragEnabled,
                connectTimeout,
                readTimeout,
                indexingTimeout,
                Math.min(indexingTimeout, 60),
                serviceSecret);
    }

    @Autowired
    public RagPipelineClient(
            @Value("${codecrow.rag.api.url:http://codecrow-rag-pipeline:8001}") String ragApiUrl,
            @Value("${codecrow.rag.api.enabled:true}") boolean ragEnabled,
            @Value("${codecrow.rag.api.timeout.connect:30}") int connectTimeout,
            @Value("${codecrow.rag.api.timeout.read:120}") int readTimeout,
            @Value("${codecrow.rag.api.timeout.indexing:14400}") int indexingTimeout,
            @Value("${codecrow.rag.api.timeout.stream-idle:60}") int streamIdleTimeout,
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

        this.streamingHttpClient = this.longRunningHttpClient.newBuilder()
                .readTimeout(
                        Math.max(1, streamIdleTimeout),
                        java.util.concurrent.TimeUnit.SECONDS)
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
            List<String> excludePatterns,
            String collectionTarget
    ) throws IOException {
        return indexRepository(
                repoPath, projectWorkspace, projectNamespace, branch, commit,
                includePatterns, excludePatterns, collectionTarget, null, null);
    }

    public Map<String, Object> indexRepository(
            String repoPath,
            String projectWorkspace,
            String projectNamespace,
            String branch,
            String commit,
            List<String> includePatterns,
            List<String> excludePatterns,
            String collectionTarget,
            String projectType,
            String sourceRoot
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
        payload.put("collection_target", requireExactTarget(
                "collectionTarget", collectionTarget));
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
        putAnalysisProfile(payload, projectType, sourceRoot);

        String url = ragApiUrl + "/index/repository";
        return postLongRunning(url, payload);
    }

    /**
     * Streaming exact-generation indexing with an explicit temporary-snapshot
     * ownership handoff. The RAG service atomically moves the snapshot before
     * it emits admission, so a lost stream cannot expose its active worker to
     * caller-side deletion of the original path.
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
            boolean transferRepositoryOwnership,
            Runnable ownershipAdmissionConsumer,
            Consumer<Map<String, Object>> progressConsumer
    ) throws IOException {
        return indexRepository(
                repoPath, projectWorkspace, projectNamespace, branch, commit,
                includePatterns, excludePatterns, collectionTarget,
                transferRepositoryOwnership, ownershipAdmissionConsumer,
                progressConsumer, null, null);
    }

    public Map<String, Object> indexRepository(
            String repoPath,
            String projectWorkspace,
            String projectNamespace,
            String branch,
            String commit,
            List<String> includePatterns,
            List<String> excludePatterns,
            String collectionTarget,
            boolean transferRepositoryOwnership,
            Runnable ownershipAdmissionConsumer,
            Consumer<Map<String, Object>> progressConsumer,
            String projectType,
            String sourceRoot
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
        payload.put("collection_target", requireExactTarget(
                "collectionTarget", collectionTarget));
        if (transferRepositoryOwnership) {
            payload.put("transfer_repo_ownership", true);
        }
        payload.put("source_tree_sha256", RepositorySourceTreeIdentity.sha256(Path.of(repoPath)));
        if (includePatterns != null && !includePatterns.isEmpty()) {
            payload.put("include_patterns", includePatterns);
        }
        if (excludePatterns != null && !excludePatterns.isEmpty()) {
            payload.put("exclude_patterns", excludePatterns);
        }
        putAnalysisProfile(payload, projectType, sourceRoot);
        return postLongRunningSse(
                ragApiUrl + "/index/repository/stream", payload,
                ownershipAdmissionConsumer, progressConsumer);
    }

    private static void putAnalysisProfile(
            Map<String, Object> payload,
            String projectType,
            String sourceRoot) {
        if (projectType != null && !projectType.isBlank()) {
            payload.put("project_type", projectType);
        }
        if (sourceRoot != null && !sourceRoot.isBlank()) {
            payload.put("source_root", sourceRoot);
        }
    }

    // ==========================================================================
    // PR-SPECIFIC OPERATIONS
    // ==========================================================================

    // ==========================================================================
    // BRANCH OPERATIONS
    // ==========================================================================
    
    /**
     * Delete all indexed data for a specific branch.
     * Does NOT delete the entire collection - only the branch's data.
     * 
     * Python endpoint: DELETE /index/{workspace}/{project}/branch/{branch}
     */
    public boolean deleteBranch(
            String workspace,
            String project,
            String branch,
            String collectionTarget,
            String generationRevision,
            String generationManifestSha256
    ) throws IOException {
        BranchDeletionOutcome outcome = deleteBranchWithOutcome(
                workspace, project, branch, collectionTarget,
                generationRevision, generationManifestSha256);
        if (outcome.failure() == BranchDeletionFailure.TRANSPORT) {
            throw new IOException(outcome.detail());
        }
        if (!outcome.successful()
                && !(outcome.statusCode() == null && "RAG disabled".equals(outcome.detail()))) {
            log.warn("Failed to delete branch data target={}: status={} detail={}",
                    outcome.targetLabel(), outcome.statusCode(), outcome.detail());
        }
        return outcome.successful();
    }

    /** Structured, non-logging branch deletion for multi-generation cleanup. */
    /**
     * Deletes one exact generation using its registry-owned revision and
     * manifest digest as an O(1) ownership proof. The RAG service retrieves the
     * sealed generation receipt; it does not scan every stored unit.
     */
    public BranchDeletionOutcome deleteBranchWithOutcome(
            String workspace,
            String project,
            String branch,
            String collectionTarget,
            String generationRevision,
            String generationManifestSha256
    ) {
        String targetLabel = requireExactTarget("collectionTarget", collectionTarget);
        String exactRevision = requireExactTarget("generationRevision", generationRevision);
        String exactManifest = requireExactTarget(
                "generationManifestSha256", generationManifestSha256);
        if (!ragEnabled) {
            return BranchDeletionOutcome.failure(
                    targetLabel, BranchDeletionFailure.TARGET, null, "RAG disabled");
        }
        
        // URL-encode branch name to handle slashes (e.g., feature/xyz -> feature%2Fxyz)
        String encodedBranch = java.net.URLEncoder.encode(branch, java.nio.charset.StandardCharsets.UTF_8);
        HttpUrl.Builder urlBuilder = HttpUrl.get(String.format(
                "%s/index/%s/%s/branch/%s", ragApiUrl, workspace, project, encodedBranch)).newBuilder();
        urlBuilder.addQueryParameter("collection_target", targetLabel);
        urlBuilder.addQueryParameter("generation_revision", exactRevision);
        urlBuilder.addQueryParameter("generation_manifest_sha256", exactManifest);
        
        Request.Builder builder = new Request.Builder()
                .url(urlBuilder.build())
                .delete();
        addAuthHeader(builder);
        Request request = builder.build();
        
        try (Response response = httpClient.newCall(request).execute()) {
            if (response.isSuccessful()) {
                log.info("Deleted branch data for {}/{}/{} target={}",
                        workspace, project, branch, targetLabel);
                return BranchDeletionOutcome.success(targetLabel);
            } else {
                int statusCode = response.code();
                String detail = response.body() != null
                        ? response.body().string()
                        : "no body";
                boolean serviceFailure = statusCode == 401
                        || statusCode == 403
                        || statusCode == 408
                        || statusCode == 409
                        || statusCode == 429
                        || statusCode >= 500;
                return BranchDeletionOutcome.failure(
                        targetLabel,
                        serviceFailure
                                ? BranchDeletionFailure.SERVICE
                                : BranchDeletionFailure.TARGET,
                        statusCode,
                        truncateDetail(detail));
            }
        } catch (IOException transportFailure) {
            return BranchDeletionOutcome.failure(
                    targetLabel,
                    BranchDeletionFailure.TRANSPORT,
                    null,
                    transportFailure.getMessage());
        }
    }

    public enum BranchDeletionFailure {
        NONE,
        TARGET,
        SERVICE,
        TRANSPORT
    }

    public record BranchDeletionOutcome(
            String targetLabel,
            boolean successful,
            BranchDeletionFailure failure,
            Integer statusCode,
            String detail) {

        public static BranchDeletionOutcome success(String targetLabel) {
            return new BranchDeletionOutcome(
                    targetLabel, true, BranchDeletionFailure.NONE, null, null);
        }

        public static BranchDeletionOutcome failure(
                String targetLabel,
                BranchDeletionFailure failure,
                Integer statusCode,
                String detail) {
            return new BranchDeletionOutcome(
                    targetLabel, false, failure, statusCode, detail);
        }

        public boolean shouldStopRemainingTargets() {
            return failure == BranchDeletionFailure.SERVICE
                    || failure == BranchDeletionFailure.TRANSPORT;
        }
    }
    
    private static String requireExactTarget(String fieldName, String value) {
        if (value == null || value.isBlank()) {
            throw new IllegalArgumentException(fieldName + " is required for exact generation cleanup");
        }
        return value;
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

    public record RepresentationIdentity(
            String identity,
            String indexRepresentationFingerprint,
            String pluginDescriptorFingerprint,
            String pluginImplementationFingerprint,
            List<String> pluginIds) {
    }

    @SuppressWarnings("unchecked")
    public RepresentationIdentity getCurrentRepresentationIdentity()
            throws IOException {
        if (!ragEnabled) {
            throw new IOException("RAG disabled");
        }
        Request.Builder builder = new Request.Builder()
                .url(ragApiUrl + "/system/representation")
                .get();
        addAuthHeader(builder);
        try (Response response = httpClient.newCall(builder.build()).execute()) {
            String body = response.body() != null ? response.body().string() : "{}";
            if (!response.isSuccessful()) {
                throw new RagApiException(response.code(), truncateDetail(body));
            }
            Map<String, Object> payload = objectMapper.readValue(body, Map.class);
            String identity = requireExactTarget(
                    "representationIdentity",
                    String.valueOf(payload.get("representation_identity")));
            Object rawPluginIds = payload.get("plugin_ids");
            List<String> pluginIds = rawPluginIds instanceof List<?> values
                    ? values.stream().map(String::valueOf).toList()
                    : List.of();
            return new RepresentationIdentity(
                    identity,
                    String.valueOf(payload.get("index_representation_fingerprint")),
                    String.valueOf(payload.get("plugin_descriptor_fingerprint")),
                    String.valueOf(payload.get("plugin_implementation_fingerprint")),
                    pluginIds);
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
            Runnable ownershipAdmissionConsumer,
            Consumer<Map<String, Object>> progressConsumer
    ) throws IOException {
        Object workspace = payload.get("workspace");
        Object project = payload.get("project");
        Object branch = payload.get("branch");
        Object commit = payload.get("commit");
        long startedNanos = System.nanoTime();
        RequestBody body = RequestBody.create(objectMapper.writeValueAsString(payload), JSON);
        Request.Builder builder = new Request.Builder()
                .url(url)
                .header("Accept", "text/event-stream")
                .post(body);
        addAuthHeader(builder);

        log.info(
                "RAG index stream starting workspace={} project={} branch={} commit={}",
                workspace, project, branch, commit);
        try (Response response = streamingHttpClient.newCall(builder.build()).execute()) {
            if (!response.isSuccessful()) {
                String detail = response.body() != null ? response.body().string() : "{}";
                throw new RagApiException(response.code(), detail);
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
                if ("admitted".equals(type)) {
                    log.info(
                            "RAG index stream admitted workspace={} project={} branch={} "
                                    + "ownership_transferred={} elapsed_ms={}",
                            workspace, project, branch,
                            event.get("repositoryOwnershipTransferred"),
                            elapsedMillis(startedNanos));
                    if (Boolean.TRUE.equals(
                            event.get("repositoryOwnershipTransferred"))
                            && ownershipAdmissionConsumer != null) {
                        ownershipAdmissionConsumer.run();
                        ownershipAdmissionConsumer = null;
                    }
                    continue;
                }
                if ("heartbeat".equals(type)) {
                    log.info(
                            "RAG index stream heartbeat workspace={} project={} branch={} "
                                    + "stage={} elapsed_ms={} idle_ms={}",
                            workspace, project, branch, event.get("stage"),
                            event.get("elapsedMs"), event.get("idleMs"));
                    continue;
                }
                if ("progress".equals(type)) {
                    log.info(
                            "RAG index stream progress workspace={} project={} branch={} "
                                    + "stage={} progress={} message={} elapsed_ms={}",
                            workspace, project, branch, event.get("stage"),
                            event.get("progress"), event.get("message"),
                            elapsedMillis(startedNanos));
                    if (progressConsumer != null) {
                        progressConsumer.accept(new LinkedHashMap<>(event));
                    }
                    continue;
                }
                if ("complete".equals(type)) {
                    Object result = event.get("result");
                    if (result instanceof Map<?, ?> resultMap) {
                        log.info(
                                "RAG index stream completed workspace={} project={} branch={} "
                                        + "commit={} documents={} chunks={} elapsed_ms={}",
                                workspace, project, branch, commit,
                                resultMap.get("document_count"),
                                resultMap.get("chunk_count"),
                                elapsedMillis(startedNanos));
                        return new LinkedHashMap<>((Map<String, Object>) resultMap);
                    }
                    throw new IOException("RAG progress stream completed without index result");
                }
                if ("error".equals(type)) {
                    throw new IOException("RAG API error: " + event.getOrDefault("message", "unknown error"));
                }
            }
        } catch (IOException | RuntimeException failure) {
            log.warn(
                    "RAG index stream failed workspace={} project={} branch={} "
                            + "commit={} elapsed_ms={}: {}",
                    workspace, project, branch, commit,
                    elapsedMillis(startedNanos), failure.getMessage());
            throw failure;
        }
        log.warn(
                "RAG index stream ended without terminal result workspace={} "
                        + "project={} branch={} commit={} elapsed_ms={}",
                workspace, project, branch, commit, elapsedMillis(startedNanos));
        throw new IOException("RAG progress stream ended without a terminal result");
    }

    private static long elapsedMillis(long startedNanos) {
        return java.util.concurrent.TimeUnit.NANOSECONDS.toMillis(
                System.nanoTime() - startedNanos);
    }

    private static String truncateDetail(String detail) {
        if (detail == null) {
            return "no detail";
        }
        return detail.length() > 500 ? detail.substring(0, 500) + "..." : detail;
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
                // Callers own contextual, rate-bounded diagnostics. Keep the
                // complete detail on the exception without logging it twice.
                log.debug("RAG API request failed: {} - {}", response.code(), responseBody);
                // Include truncated response body in exception so callers can see the actual error
                String detail = responseBody.length() > 500
                        ? responseBody.substring(0, 500) + "..."
                        : responseBody;
                throw new RagApiException(response.code(), detail);
            }

            return objectMapper.readValue(responseBody, Map.class);
        }
    }
}
