package org.rostilos.codecrow.mcp.bitbucket.cloud;

import com.fasterxml.jackson.databind.JsonNode;
import com.fasterxml.jackson.databind.ObjectMapper;
import com.fasterxml.jackson.databind.type.CollectionType;
import okhttp3.*;
import org.rostilos.codecrow.mcp.bitbucket.BitbucketCloudException;
import org.rostilos.codecrow.mcp.bitbucket.BitbucketConfiguration;
import org.rostilos.codecrow.mcp.bitbucket.cloud.model.BitbucketProjectBranchingModel;
import org.rostilos.codecrow.mcp.bitbucket.cloud.model.*;
import org.rostilos.codecrow.mcp.bitbucket.pullrequest.diff.RawDiffParser;
import org.rostilos.codecrow.mcp.bitbucket.pullrequest.diff.FileDiff;
import org.rostilos.codecrow.mcp.util.TokenLimitGuard;
import org.slf4j.Logger;
import org.slf4j.LoggerFactory;

import java.io.IOException;
import java.net.URLEncoder;
import java.nio.charset.StandardCharsets;
import java.util.Base64;
import java.util.List;
import java.util.Map;
import java.util.Optional;
import java.util.HashMap;
import java.util.stream.Collectors;

public class BitbucketCloudClientImpl implements BitbucketCloudClient {

    private static final Logger LOGGER = LoggerFactory.getLogger(BitbucketCloudClientImpl.class);
    private static final MediaType APPLICATION_JSON_MEDIA_TYPE = MediaType.get("application/json");
    private JsonNode pullRequestJsonCache = null;
    private final int fileLimit;
    private final String appId;
    private final String almRepo;
    private final String prNumber;
    private final ObjectMapper objectMapper;

    private final OkHttpClient okHttpClient;
    private final BitbucketConfiguration bitbucketConfiguration;

    BitbucketCloudClientImpl(
            OkHttpClient okHttpClient,
            BitbucketConfiguration bitbucketConfiguration,
            int fileLimit,
            String appId,
            String almRepo,
            String prNumber
    ) {
        this.okHttpClient = okHttpClient;
        this.bitbucketConfiguration = bitbucketConfiguration;
        this.fileLimit = fileLimit;
        this.appId = appId;
        this.almRepo = almRepo;
        this.prNumber = prNumber;
        this.objectMapper = new ObjectMapper(); // Initialize ObjectMapper
    }

    //TODO: use vcs-client package
    static String negotiateBearerToken(String clientId, String clientSecret, ObjectMapper objectMapper, OkHttpClient okHttpClient) {
        Request request = new Request.Builder()
                .header("Authorization", "Basic " + Base64.getEncoder().encodeToString((clientId + ":" + clientSecret).getBytes(StandardCharsets.UTF_8)))
                .url("https://bitbucket.org/site/oauth2/access_token")
                .post(RequestBody.create("grant_type=client_credentials", MediaType.parse("application/x-www-form-urlencoded")))
                .build();

        try (Response response = okHttpClient.newCall(request).execute()) {
            if (!response.isSuccessful()) {
                throw new IllegalStateException("Failed to authenticate: " + response.code() + " - " + response.body().string());
            }

            assert response.body() != null;
            JsonNode node = objectMapper.readTree(response.body().string());
            return node.get("access_token").asText();
        } catch (IOException ex) {
            throw new IllegalStateException("Could not retrieve bearer token", ex);
        }
    }

    //TODO: use that later to retrieve files and filter it via project inclusions/exclusions
    @Override
    public List<FileDiff> getPullRequestChanges() throws IOException {
        String apiUrl = String.format(
                "https://api.bitbucket.org/2.0/repositories/%s/%s/pullrequests/%s/diff",
                appId, almRepo, prNumber
        );

        Request req = new Request.Builder()
                .get()
                .url(apiUrl)
                .build();
        List<FileDiff> fileDiffs;

        try (Response response = okHttpClient.newCall(req).execute()) {
            String rawDiff = validate(response);
            RawDiffParser diffParser = new RawDiffParser();
            fileDiffs = diffParser.execute(rawDiff);

            //fetch original content
            String targetBranch = getTargetBranch();
            if (targetBranch == null || targetBranch.isEmpty()) {
                LOGGER.error("Error fetching pull request info, target branch");
                throw new BitbucketCloudException("Error fetching pull request info, target branch");
            }
            int fileCount = 0;
            for (FileDiff fileDiff : fileDiffs) {
                if (fileLimit > 0 && fileCount >= fileLimit) {
                    LOGGER.info("Reached file limit of {}. Skipping remaining files.", fileLimit);
                    break;
                }

                if (fileDiff.getDiffType() != FileDiff.DiffType.ADDED) {
                    String fileRawContentFromBitbucket = fetchFileContent(targetBranch, fileDiff.getFilePath());
                    fileDiff.setRawContent(fileRawContentFromBitbucket);
                } else {
                    fileDiff.setRawContent("There is no previous version, probably a new file");
                }
                fileCount++;
            }
        }
        return fileDiffs;
    }

    private String getTargetBranch() throws IOException {
        JsonNode root = getPullRequestJson();
        JsonNode nameNode = root.path("destination").path("branch").path("name");
        if (nameNode.isMissingNode()) {
            throw new IOException("Missing 'destination.branch.name' in response");
        }
        return nameNode.asText();
    }

    private JsonNode getPullRequestJson() throws IOException {
        if (pullRequestJsonCache != null) {
            return pullRequestJsonCache;
        }

        String apiUrl = String.format(
                "https://api.bitbucket.org/2.0/repositories/%s/%s/pullrequests/%s",
                appId, almRepo, prNumber
        );

        Request req = new Request.Builder()
                .get()
                .url(apiUrl)
                .build();

        try (Response response = okHttpClient.newCall(req).execute()) {
            if (!response.isSuccessful()) {
                throw new IOException("Unexpected response code: " + response.code());
            }

            try (ResponseBody responseBody = response.body()) {
                if (responseBody == null) {
                    throw new IOException("Empty response body");
                }

                pullRequestJsonCache = objectMapper.readTree(responseBody.string());
                return pullRequestJsonCache;
            }
        }
    }

    private String fetchFileContent(String branch, String filePath) {
        try {
            String encodedFilePath = URLEncoder.encode(filePath, StandardCharsets.UTF_8.toString())
                    .replace("+", "%20");

            String apiUrl = String.format(
                    "https://api.bitbucket.org/2.0/repositories/%s/%s/src/%s/%s",
                    appId, almRepo, branch, encodedFilePath
            );

            Request request = new Request.Builder()
                    .url(apiUrl)
                    .get()
                    .build();

            try (Response response = okHttpClient.newCall(request).execute()) {
                return validate(response);
            }
        } catch (IOException e) {
            LOGGER.error("IO error fetching file content from Bitbucket: {}", filePath, e);
            return "Error fetching file content: " + e.getMessage();
        } catch (Exception e) {
            LOGGER.error("Unexpected error fetching file content from Bitbucket: {}", filePath, e);
            return "Error fetching file content: " + e.getMessage();
        }
    }

    public String getPrNumber() {
        return prNumber;
    }

    @Override
    public String getPullRequestTitle() throws IOException {
        JsonNode root = getPullRequestJson();

        JsonNode titleNode = root.path("title");
        if (titleNode.isMissingNode()) {
            throw new IOException("Missing 'title' in response");
        }
        return titleNode.asText();
    }

    @Override
    public String getPullRequestDescription() throws IOException {
        JsonNode root = getPullRequestJson();

        JsonNode descriptionNode = root.path("description");
        if (descriptionNode.isMissingNode() || descriptionNode.isNull()) {
            return "";
        }
        return descriptionNode.asText();
    }

    @Override
    public List<BitbucketRepository> listRepositories(String workspace, Integer limit) throws IOException {
        String ws = Optional.ofNullable(workspace).orElse(bitbucketConfiguration.getWorkspace());
        if (ws == null || ws.isEmpty()) {
            throw new IllegalArgumentException("Workspace must be provided either as a parameter or through configuration.");
        }

        String apiUrl = String.format("https://api.bitbucket.org/2.0/repositories/%s", ws);
        HttpUrl.Builder urlBuilder = HttpUrl.parse(apiUrl).newBuilder();
        if (limit != null && limit > 0) {
            urlBuilder.addQueryParameter("limit", String.valueOf(limit));
        }

        Request request = new Request.Builder()
                .url(urlBuilder.build())
                .get()
                .build();

        try (Response response = okHttpClient.newCall(request).execute()) {
            String responseBody = validate(response);
            JsonNode root = objectMapper.readTree(responseBody);
            JsonNode values = root.path("values");
            if (values.isArray()) {
                CollectionType type = objectMapper.getTypeFactory().constructCollectionType(List.class, BitbucketRepository.class);
                return objectMapper.readValue(values.traverse(), type);
            }
            return List.of();
        }
    }

    @Override
    public BitbucketRepository getRepository(String workspace, String repoSlug) throws IOException {
        String ws = Optional.ofNullable(workspace).orElse(bitbucketConfiguration.getWorkspace());
        if (ws == null || ws.isEmpty()) {
            throw new IllegalArgumentException("Workspace must be provided either as a parameter or through configuration.");
        }

        String apiUrl = String.format("https://api.bitbucket.org/2.0/repositories/%s/%s", ws, repoSlug);
        Request request = new Request.Builder()
                .url(apiUrl)
                .get()
                .build();

        try (Response response = okHttpClient.newCall(request).execute()) {
            String responseBody = validate(response);
            return objectMapper.readValue(responseBody, BitbucketRepository.class);
        }
    }

    @Override
    public List<BitbucketPullRequest> getPullRequests(String workspace, String repoSlug, String state, Integer limit) throws IOException {
        String ws = Optional.ofNullable(workspace).orElse(bitbucketConfiguration.getWorkspace());
        if (ws == null || ws.isEmpty()) {
            throw new IllegalArgumentException("Workspace must be provided either as a parameter or through configuration.");
        }

        String apiUrl = String.format("https://api.bitbucket.org/2.0/repositories/%s/%s/pullrequests", ws, repoSlug);
        HttpUrl.Builder urlBuilder = HttpUrl.parse(apiUrl).newBuilder();
        if (state != null && !state.isEmpty()) {
            urlBuilder.addQueryParameter("state", state);
        }
        if (limit != null && limit > 0) {
            urlBuilder.addQueryParameter("limit", String.valueOf(limit));
        }

        Request request = new Request.Builder()
                .url(urlBuilder.build())
                .get()
                .build();

        try (Response response = okHttpClient.newCall(request).execute()) {
            String responseBody = validate(response);
            JsonNode root = objectMapper.readTree(responseBody);
            JsonNode values = root.path("values");
            if (values.isArray()) {
                CollectionType type = objectMapper.getTypeFactory().constructCollectionType(List.class, BitbucketPullRequest.class);
                return objectMapper.readValue(values.traverse(), type);
            }
            return List.of();
        }
    }

    @Override
    public BitbucketPullRequest createPullRequest(String workspace, String repoSlug, String title, String description, String sourceBranch, String targetBranch, List<String> reviewers) throws IOException {
        String ws = Optional.ofNullable(workspace).orElse(bitbucketConfiguration.getWorkspace());
        if (ws == null || ws.isEmpty()) {
            throw new IllegalArgumentException("Workspace must be provided either as a parameter or through configuration.");
        }

        String apiUrl = String.format("https://api.bitbucket.org/2.0/repositories/%s/%s/pullrequests", ws, repoSlug);

        List<Map<String, String>> reviewersArray = null;
        if (reviewers != null) {
            reviewersArray = reviewers.stream()
                    .map(username -> Map.of("username", username))
                    .toList();
        }

        Map<String, Object> requestBody = new HashMap<>();
        requestBody.put("title", title);
        requestBody.put("description", description);
        requestBody.put("source", Map.of("branch", Map.of("name", sourceBranch)));
        requestBody.put("destination", Map.of("branch", Map.of("name", targetBranch)));
        if (reviewersArray != null) {
            requestBody.put("reviewers", reviewersArray);
        }
        requestBody.put("close_source_branch", true);

        RequestBody body = RequestBody.create(objectMapper.writeValueAsString(requestBody), APPLICATION_JSON_MEDIA_TYPE);

        Request request = new Request.Builder()
                .url(apiUrl)
                .post(body)
                .build();

        try (Response response = okHttpClient.newCall(request).execute()) {
            String responseBody = validate(response);
            return objectMapper.readValue(responseBody, BitbucketPullRequest.class);
        }
    }

    @Override
    public BitbucketPullRequest getPullRequest(String workspace, String repoSlug, String pullRequestId) throws IOException {
        String ws = Optional.ofNullable(workspace).orElse(bitbucketConfiguration.getWorkspace());
        if (ws == null || ws.isEmpty()) {
            throw new IllegalArgumentException("Workspace must be provided either as a parameter or through configuration.");
        }

        String apiUrl = String.format("https://api.bitbucket.org/2.0/repositories/%s/%s/pullrequests/%s", ws, repoSlug, pullRequestId);
        Request request = new Request.Builder()
                .url(apiUrl)
                .get()
                .build();

        try (Response response = okHttpClient.newCall(request).execute()) {
            String responseBody = validate(response);
            return objectMapper.readValue(responseBody, BitbucketPullRequest.class);
        }
    }

    @Override
    public BitbucketPullRequest updatePullRequest(String workspace, String repoSlug, String pullRequestId, String title, String description) throws IOException {
        String ws = Optional.ofNullable(workspace).orElse(bitbucketConfiguration.getWorkspace());
        if (ws == null || ws.isEmpty()) {
            throw new IllegalArgumentException("Workspace must be provided either as a parameter or through configuration.");
        }

        String apiUrl = String.format("https://api.bitbucket.org/2.0/repositories/%s/%s/pullrequests/%s", ws, repoSlug, pullRequestId);

        Map<String, Object> updateData = new HashMap<>();
        if (title != null) updateData.put("title", title);
        if (description != null) updateData.put("description", description);

        RequestBody body = RequestBody.create(objectMapper.writeValueAsString(updateData), APPLICATION_JSON_MEDIA_TYPE);

        Request request = new Request.Builder()
                .url(apiUrl)
                .put(body)
                .build();

        try (Response response = okHttpClient.newCall(request).execute()) {
            String responseBody = validate(response);
            return objectMapper.readValue(responseBody, BitbucketPullRequest.class);
        }
    }

    @Override
    public Object getPullRequestActivity(String workspace, String repoSlug, String pullRequestId) throws IOException {
        String ws = Optional.ofNullable(workspace).orElse(bitbucketConfiguration.getWorkspace());
        if (ws == null || ws.isEmpty()) {
            throw new IllegalArgumentException("Workspace must be provided either as a parameter or through configuration.");
        }

        String apiUrl = String.format("https://api.bitbucket.org/2.0/repositories/%s/%s/pullrequests/%s/activity", ws, repoSlug, pullRequestId);
        Request request = new Request.Builder()
                .url(apiUrl)
                .get()
                .build();

        try (Response response = okHttpClient.newCall(request).execute()) {
            String responseBody = validate(response);
            return objectMapper.readTree(responseBody); // Return as JsonNode for now
        }
    }

    @Override
    public Object approvePullRequest(String workspace, String repoSlug, String pullRequestId) throws IOException {
        String ws = Optional.ofNullable(workspace).orElse(bitbucketConfiguration.getWorkspace());
        if (ws == null || ws.isEmpty()) {
            throw new IllegalArgumentException("Workspace must be provided either as a parameter or through configuration.");
        }

        String apiUrl = String.format("https://api.bitbucket.org/2.0/repositories/%s/%s/pullrequests/%s/approve", ws, repoSlug, pullRequestId);
        RequestBody body = RequestBody.create("", null); // Empty body for POST

        Request request = new Request.Builder()
                .url(apiUrl)
                .post(body)
                .build();

        try (Response response = okHttpClient.newCall(request).execute()) {
            String responseBody = validate(response);
            return objectMapper.readTree(responseBody); // Return as JsonNode for now
        }
    }

    @Override
    public Object unapprovePullRequest(String workspace, String repoSlug, String pullRequestId) throws IOException {
        String ws = Optional.ofNullable(workspace).orElse(bitbucketConfiguration.getWorkspace());
        if (ws == null || ws.isEmpty()) {
            throw new IllegalArgumentException("Workspace must be provided either as a parameter or through configuration.");
        }

        String apiUrl = String.format("https://api.bitbucket.org/2.0/repositories/%s/%s/pullrequests/%s/approve", ws, repoSlug, pullRequestId);

        Request request = new Request.Builder()
                .url(apiUrl)
                .delete()
                .build();

        try (Response response = okHttpClient.newCall(request).execute()) {
            validate(response);
            return "Pull request approval removed successfully.";
        }
    }

    @Override
    public Object declinePullRequest(String workspace, String repoSlug, String pullRequestId, String message) throws IOException {
        String ws = Optional.ofNullable(workspace).orElse(bitbucketConfiguration.getWorkspace());
        if (ws == null || ws.isEmpty()) {
            throw new IllegalArgumentException("Workspace must be provided either as a parameter or through configuration.");
        }

        String apiUrl = String.format("https://api.bitbucket.org/2.0/repositories/%s/%s/pullrequests/%s/decline", ws, repoSlug, pullRequestId);

        Map<String, Object> requestBody = new HashMap<>();
        if (message != null) {
            requestBody.put("message", message);
        }
        RequestBody body = RequestBody.create(objectMapper.writeValueAsString(requestBody), APPLICATION_JSON_MEDIA_TYPE);

        Request request = new Request.Builder()
                .url(apiUrl)
                .post(body)
                .build();

        try (Response response = okHttpClient.newCall(request).execute()) {
            String responseBody = validate(response);
            return objectMapper.readTree(responseBody); // Return as JsonNode for now
        }
    }

    @Override
    public Object mergePullRequest(String workspace, String repoSlug, String pullRequestId, String message, String strategy) throws IOException {
        String ws = Optional.ofNullable(workspace).orElse(bitbucketConfiguration.getWorkspace());
        if (ws == null || ws.isEmpty()) {
            throw new IllegalArgumentException("Workspace must be provided either as a parameter or through configuration.");
        }

        String apiUrl = String.format("https://api.bitbucket.org/2.0/repositories/%s/%s/pullrequests/%s/merge", ws, repoSlug, pullRequestId);

        Map<String, Object> requestBody = new HashMap<>();
        if (message != null) {
            requestBody.put("message", message);
        }
        if (strategy != null) {
            requestBody.put("merge_strategy", strategy);
        }
        RequestBody body = RequestBody.create(objectMapper.writeValueAsString(requestBody), APPLICATION_JSON_MEDIA_TYPE);

        Request request = new Request.Builder()
                .url(apiUrl)
                .post(body)
                .build();

        try (Response response = okHttpClient.newCall(request).execute()) {
            String responseBody = validate(response);
            return objectMapper.readTree(responseBody); // Return as JsonNode for now
        }
    }

    @Override
    public Object getPullRequestComments(String workspace, String repoSlug, String pullRequestId) throws IOException {
        String ws = Optional.ofNullable(workspace).orElse(bitbucketConfiguration.getWorkspace());
        if (ws == null || ws.isEmpty()) {
            throw new IllegalArgumentException("Workspace must be provided either as a parameter or through configuration.");
        }

        String apiUrl = String.format("https://api.bitbucket.org/2.0/repositories/%s/%s/pullrequests/%s/comments", ws, repoSlug, pullRequestId);
        Request request = new Request.Builder()
                .url(apiUrl)
                .get()
                .build();

        try (Response response = okHttpClient.newCall(request).execute()) {
            String responseBody = validate(response);
            return objectMapper.readTree(responseBody); // Return as JsonNode for now
        }
    }

    @Override
    public String getPullRequestDiff(String workspace, String repoSlug, String pullRequestId) throws IOException {
        String apiUrl = String.format("https://api.bitbucket.org/2.0/repositories/%s/%s/pullrequests/%s/diff", workspace, repoSlug, pullRequestId);
        Request request = new Request.Builder()
                .url(apiUrl)
                .header("Accept", "text/plain")  // Required for Bitbucket diff endpoint to avoid 406
                .get()
                .build();

        try (Response response = okHttpClient.newCall(request).execute()) {
            return validate(response, true);
        }
    }

    @Override
    public Object getPullRequestCommits(String workspace, String repoSlug, String pullRequestId) throws IOException {
        String ws = Optional.ofNullable(workspace).orElse(bitbucketConfiguration.getWorkspace());
        if (ws == null || ws.isEmpty()) {
            throw new IllegalArgumentException("Workspace must be provided either as a parameter or through configuration.");
        }

        String apiUrl = String.format("https://api.bitbucket.org/2.0/repositories/%s/%s/pullrequests/%s/commits", ws, repoSlug, pullRequestId);
        Request request = new Request.Builder()
                .url(apiUrl)
                .get()
                .build();

        try (Response response = okHttpClient.newCall(request).execute()) {
            String responseBody = validate(response);
            return objectMapper.readTree(responseBody); // Return as JsonNode for now
        }
    }

    @Override
    public BitbucketBranchingModel getRepositoryBranchingModel(String workspace, String repoSlug) throws IOException {
        String ws = Optional.ofNullable(workspace).orElse(bitbucketConfiguration.getWorkspace());
        if (ws == null || ws.isEmpty()) {
            throw new IllegalArgumentException("Workspace must be provided either as a parameter or through configuration.");
        }

        String apiUrl = String.format("https://api.bitbucket.org/2.0/repositories/%s/%s/branching-model", ws, repoSlug);
        Request request = new Request.Builder()
                .url(apiUrl)
                .get()
                .build();

        try (Response response = okHttpClient.newCall(request).execute()) {
            String responseBody = validate(response);
            return objectMapper.readValue(responseBody, BitbucketBranchingModel.class);
        }
    }

    @Override
    public BitbucketBranchingModelSettings getRepositoryBranchingModelSettings(String workspace, String repoSlug) throws IOException {
        String ws = Optional.ofNullable(workspace).orElse(bitbucketConfiguration.getWorkspace());
        if (ws == null || ws.isEmpty()) {
            throw new IllegalArgumentException("Workspace must be provided either as a parameter or through configuration.");
        }

        String apiUrl = String.format("https://api.bitbucket.org/2.0/repositories/%s/%s/branching-model/settings", ws, repoSlug);
        Request request = new Request.Builder()
                .url(apiUrl)
                .get()
                .build();

        try (Response response = okHttpClient.newCall(request).execute()) {
            String responseBody = validate(response);
            return objectMapper.readValue(responseBody, BitbucketBranchingModelSettings.class);
        }
    }

    @Override
    public BitbucketBranchingModelSettings updateRepositoryBranchingModelSettings(String workspace, String repoSlug, Map<String, Object> development, Map<String, Object> production, List<Map<String, Object>> branchTypes) throws IOException {
        String ws = Optional.ofNullable(workspace).orElse(bitbucketConfiguration.getWorkspace());
        if (ws == null || ws.isEmpty()) {
            throw new IllegalArgumentException("Workspace must be provided either as a parameter or through configuration.");
        }

        String apiUrl = String.format("https://api.bitbucket.org/2.0/repositories/%s/%s/branching-model/settings", ws, repoSlug);

        Map<String, Object> requestBody = new HashMap<>();
        if (development != null) requestBody.put("development", development);
        if (production != null) requestBody.put("production", production);
        if (branchTypes != null) requestBody.put("branch_types", branchTypes);

        RequestBody body = RequestBody.create(objectMapper.writeValueAsString(requestBody), APPLICATION_JSON_MEDIA_TYPE);

        Request request = new Request.Builder()
                .url(apiUrl)
                .put(body)
                .build();

        try (Response response = okHttpClient.newCall(request).execute()) {
            String responseBody = validate(response);
            return objectMapper.readValue(responseBody, BitbucketBranchingModelSettings.class);
        }
    }

    @Override
    public BitbucketBranchingModel getEffectiveRepositoryBranchingModel(String workspace, String repoSlug) throws IOException {
        String ws = Optional.ofNullable(workspace).orElse(bitbucketConfiguration.getWorkspace());
        if (ws == null || ws.isEmpty()) {
            throw new IllegalArgumentException("Workspace must be provided either as a parameter or through configuration.");
        }

        String apiUrl = String.format("https://api.bitbucket.org/2.0/repositories/%s/%s/effective-branching-model", ws, repoSlug);
        Request request = new Request.Builder()
                .url(apiUrl)
                .get()
                .build();

        try (Response response = okHttpClient.newCall(request).execute()) {
            String responseBody = validate(response);
            return objectMapper.readValue(responseBody, BitbucketBranchingModel.class);
        }
    }

    @Override
    public BitbucketProjectBranchingModel getProjectBranchingModel(String workspace, String projectKey) throws IOException {
        String ws = Optional.ofNullable(workspace).orElse(bitbucketConfiguration.getWorkspace());
        if (ws == null || ws.isEmpty()) {
            throw new IllegalArgumentException("Workspace must be provided either as a parameter or through configuration.");
        }

        String apiUrl = String.format("https://api.bitbucket.org/2.0/workspaces/%s/projects/%s/branching-model", ws, projectKey);
        Request request = new Request.Builder()
                .url(apiUrl)
                .get()
                .build();

        try (Response response = okHttpClient.newCall(request).execute()) {
            String responseBody = validate(response);
            return objectMapper.readValue(responseBody, BitbucketProjectBranchingModel.class);
        }
    }

    @Override
    public BitbucketProjectBranchingModel getProjectBranchingModelSettings(String workspace, String projectKey) throws IOException {
        String ws = Optional.ofNullable(workspace).orElse(bitbucketConfiguration.getWorkspace());
        if (ws == null || ws.isEmpty()) {
            throw new IllegalArgumentException("Workspace must be provided either as a parameter or through configuration.");
        }

        String apiUrl = String.format("https://api.bitbucket.org/2.0/workspaces/%s/projects/%s/branching-model/settings", ws, projectKey);
        Request request = new Request.Builder()
                .url(apiUrl)
                .get()
                .build();

        try (Response response = okHttpClient.newCall(request).execute()) {
            String responseBody = validate(response);
            return objectMapper.readValue(responseBody, BitbucketProjectBranchingModel.class);
        }
    }

    @Override
    public BitbucketProjectBranchingModel updateProjectBranchingModelSettings(String workspace, String projectKey, Map<String, Object> development, Map<String, Object> production, List<Map<String, Object>> branchTypes) throws IOException {
        String ws = Optional.ofNullable(workspace).orElse(bitbucketConfiguration.getWorkspace());
        if (ws == null || ws.isEmpty()) {
            throw new IllegalArgumentException("Workspace must be provided either as a parameter or through configuration.");
        }

        String apiUrl = String.format("https://api.bitbucket.org/2.0/workspaces/%s/projects/%s/branching-model/settings", ws, projectKey);

        Map<String, Object> requestBody = new HashMap<>();
        if (development != null) requestBody.put("development", development);
        if (production != null) requestBody.put("production", production);
        if (branchTypes != null) requestBody.put("branch_types", branchTypes);

        RequestBody body = RequestBody.create(objectMapper.writeValueAsString(requestBody), APPLICATION_JSON_MEDIA_TYPE);

        Request request = new Request.Builder()
                .url(apiUrl)
                .put(body)
                .build();

        try (Response response = okHttpClient.newCall(request).execute()) {
            String responseBody = validate(response);
            return objectMapper.readValue(responseBody, BitbucketProjectBranchingModel.class);
        }
    }

    @Override
    public String getBranchFileContent(String workspace, String repoSlug, String branch, String filePath) throws IOException {
        String ws = Optional.ofNullable(workspace).orElse(bitbucketConfiguration.getWorkspace());
        if (ws == null || ws.isEmpty()) {
            throw new IllegalArgumentException("Workspace must be provided either as a parameter or through configuration.");
        }

        String apiUrl = String.format("https://api.bitbucket.org/2.0/repositories/%s/%s/src/%s/%s", ws, repoSlug, branch, filePath);
        Request request = new Request.Builder()
                .url(apiUrl)
                .get()
                .build();

        try (Response response = okHttpClient.newCall(request).execute()) {
            return validate(response);
        }
    }

    @Override
    public String getRootDirectory(String workspace, String projectKey, String branch) throws IOException {
        String apiUrl = String.format("https://api.bitbucket.org/2.0/repositories/%s/%s/src/%s/", workspace, projectKey, branch);
        LOGGER.info(apiUrl);
        Request request = new Request.Builder()
                .url(apiUrl)
                .get()
                .build();

        try (Response response = okHttpClient.newCall(request).execute()) {
            return validate(response);
        }
    }

    @Override
    public String getDirectoryByPath(String workspace, String projectKey, String branch, String dirPath) throws IOException {
        String apiUrl = String.format("https://api.bitbucket.org/2.0/repositories/%s/%s/src/%s/%s", workspace, projectKey, branch, dirPath);
        LOGGER.info(apiUrl);
        Request request = new Request.Builder()
                .url(apiUrl)
                .get()
                .build();

        try (Response response = okHttpClient.newCall(request).execute()) {
            return validate(response);
        }
    }

    private String validate(Response response, boolean validateByTokenSize) throws IOException {
        if (!response.isSuccessful()) {
            String error = Optional.ofNullable(response.body()).map(b -> {
                try {
                    return b.string();
                } catch (IOException e) {
                    throw new IllegalStateException("Could not retrieve response content", e);
                }
            }).orElse("Request failed but Bitbucket didn't respond with a proper error message");
            throw new IOException(error);
        } else {
            assert response.body() != null;
            String responseText = response.body().string();
            if(validateByTokenSize &&  TokenLimitGuard.isExceededMaxAllowedTokens(responseText)) {
                return "Response received successfully, but length exceeds the set token limit";
            }
            return responseText;
        }
    }

    private String validate(Response response) throws IOException {
        return validate(response, false); // default value for validateByTokenSize
    }
}
