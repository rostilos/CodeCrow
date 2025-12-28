package org.rostilos.codecrow.vcsclient.bitbucket.cloud.actions;

import com.fasterxml.jackson.databind.JsonNode;
import com.fasterxml.jackson.databind.ObjectMapper;
import okhttp3.*;
import org.rostilos.codecrow.core.model.vcs.VcsRepoInfo;
import org.rostilos.codecrow.vcsclient.bitbucket.model.comment.BitbucketCommentContent;
import org.rostilos.codecrow.vcsclient.bitbucket.model.comment.BitbucketSummarizeComment;
import org.slf4j.Logger;
import org.slf4j.LoggerFactory;

import java.io.IOException;
import java.util.Optional;

//TODO: bstract or a single client class for actions
public class CommentOnBitbucketCloudAction {
    private final OkHttpClient authorizedOkHttpClient;
    private final VcsRepoInfo vcsRepoInfo;
    private final Long prNumber;

    private static final MediaType APPLICATION_JSON_MEDIA_TYPE = MediaType.get("application/json");
    private static final Logger LOGGER = LoggerFactory.getLogger(CommentOnBitbucketCloudAction.class);
    private static final String AI_SUMMARIZE_MARKER = "[Codecrow AI Summarize]";

    public CommentOnBitbucketCloudAction(
            OkHttpClient authorizedOkHttpClient,
            VcsRepoInfo vcsRepoInfo,
            Long prNumber
    ) {
        this.authorizedOkHttpClient = authorizedOkHttpClient;
        this.vcsRepoInfo = vcsRepoInfo;
        this.prNumber = prNumber;
    }

    public void postSummaryResult(String textContent) throws IOException {
        String workspace = vcsRepoInfo.getRepoWorkspace();
        String repoSlug = vcsRepoInfo.getRepoSlug();

        // Validate that we have proper workspace/repo values (not UUIDs with braces)
        if (workspace != null && workspace.startsWith("{") && workspace.endsWith("}")) {
            LOGGER.error("Invalid workspace format (UUID with braces): {}. Expected workspace slug.", workspace);
            throw new IOException("Invalid workspace format. VCS binding has UUID instead of workspace slug: " + workspace);
        }
        if (repoSlug != null && repoSlug.startsWith("{") && repoSlug.endsWith("}")) {
            LOGGER.error("Invalid repoSlug format (UUID with braces): {}. Expected repository slug.", repoSlug);
            throw new IOException("Invalid repository format. VCS binding has UUID instead of repo slug: " + repoSlug);
        }

        textContent = AI_SUMMARIZE_MARKER + "\n" + textContent;

        deleteOldSummarizeComments();
        ObjectMapper objectMapper = new ObjectMapper();

        BitbucketCommentContent commentContent = new BitbucketCommentContent(textContent);
        BitbucketSummarizeComment summarizeReport = createSummarizeComment(commentContent);

        String body = objectMapper.writeValueAsString(summarizeReport);
        String apiUrl = String.format("https://api.bitbucket.org/2.0/repositories/%s/%s/pullrequests/%s/comments", workspace, repoSlug, prNumber);
        Request req = new Request.Builder()
                .post(RequestBody.create(body, APPLICATION_JSON_MEDIA_TYPE))
                .url(apiUrl)
                .build();

        LOGGER.info("Create report on bitbucket cloud: {}", apiUrl);

        try (Response response = authorizedOkHttpClient.newCall(req).execute()) {
            validate(response);
        }
    }
    
    /**
     * Post a reply to an existing comment.
     * Bitbucket Cloud uses the parent.id field to indicate this is a reply.
     */
    public String postCommentReply(String parentCommentId, String textContent) throws IOException {
        String workspace = vcsRepoInfo.getRepoWorkspace();
        String repoSlug = vcsRepoInfo.getRepoSlug();

        // Validate workspace/repo values
        if (workspace != null && workspace.startsWith("{") && workspace.endsWith("}")) {
            throw new IOException("Invalid workspace format: " + workspace);
        }
        if (repoSlug != null && repoSlug.startsWith("{") && repoSlug.endsWith("}")) {
            throw new IOException("Invalid repository format: " + repoSlug);
        }

        ObjectMapper objectMapper = new ObjectMapper();

        // Bitbucket API format: {"content": {"raw": "text"}, "parent": {"id": 123}}
        String body = objectMapper.writeValueAsString(new java.util.HashMap<String, Object>() {{
            put("content", new java.util.HashMap<String, String>() {{
                put("raw", textContent);
            }});
            put("parent", new java.util.HashMap<String, Object>() {{
                put("id", Integer.parseInt(parentCommentId));
            }});
        }});

        String apiUrl = String.format("https://api.bitbucket.org/2.0/repositories/%s/%s/pullrequests/%s/comments", 
            workspace, repoSlug, prNumber);
        
        Request req = new Request.Builder()
                .post(RequestBody.create(body, APPLICATION_JSON_MEDIA_TYPE))
                .url(apiUrl)
                .build();

        LOGGER.info("Posting reply to comment {} on bitbucket cloud: {}", parentCommentId, apiUrl);

        try (Response response = authorizedOkHttpClient.newCall(req).execute()) {
            String responseBody = validate(response);

            JsonNode root = objectMapper.readTree(responseBody);
            if (root.has("id")) {
                return root.get("id").asText();
            }
            return "bitbucket-reply-" + System.currentTimeMillis();
        }
    }
    
    /**
     * Post a simple comment without the summarize marker or deletion.
     */
    public String postSimpleComment(String textContent) throws IOException {
        String workspace = vcsRepoInfo.getRepoWorkspace();
        String repoSlug = vcsRepoInfo.getRepoSlug();

        ObjectMapper objectMapper = new ObjectMapper();
        BitbucketCommentContent commentContent = new BitbucketCommentContent(textContent);
        BitbucketSummarizeComment comment = createSummarizeComment(commentContent);

        String body = objectMapper.writeValueAsString(comment);
        String apiUrl = String.format("https://api.bitbucket.org/2.0/repositories/%s/%s/pullrequests/%s/comments", 
            workspace, repoSlug, prNumber);
        
        Request req = new Request.Builder()
                .post(RequestBody.create(body, APPLICATION_JSON_MEDIA_TYPE))
                .url(apiUrl)
                .build();

        LOGGER.info("Posting comment to bitbucket cloud: {}", apiUrl);

        try (Response response = authorizedOkHttpClient.newCall(req).execute()) {
            String responseBody = validate(response);
            JsonNode root = objectMapper.readTree(responseBody);
            if (root.has("id")) {
                return root.get("id").asText();
            }
            return "bitbucket-comment-" + System.currentTimeMillis();
        }
    }

    private void deleteComment(JsonNode comment) throws IOException {
        JsonNode content = comment.path("content").path("raw");
        if (content.asText().contains(AI_SUMMARIZE_MARKER)) {
            String deleteUrl = comment.path("links").path("self").path("href").asText();

            Request deleteRequest = new Request.Builder()
                    .delete()
                    .url(deleteUrl)
                    .build();

            try (Response deleteResponse = authorizedOkHttpClient.newCall(deleteRequest).execute()) {
                if (deleteResponse.isSuccessful()) {
                    LOGGER.debug("Deleted comment ID: {}", comment.get("id").asInt());
                } else {
                    LOGGER.debug("Failed to delete comment ID: {}", comment.get("id").asInt());
                }
            }
        }
    }
    
    /**
     * Delete a comment by its ID.
     */
    public void deleteCommentById(String commentId) throws IOException {
        String workspace = vcsRepoInfo.getRepoWorkspace();
        String repoSlug = vcsRepoInfo.getRepoSlug();
        
        String deleteUrl = String.format(
            "https://api.bitbucket.org/2.0/repositories/%s/%s/pullrequests/%s/comments/%s",
            workspace, repoSlug, prNumber, commentId
        );

        Request deleteRequest = new Request.Builder()
                .delete()
                .url(deleteUrl)
                .build();

        try (Response deleteResponse = authorizedOkHttpClient.newCall(deleteRequest).execute()) {
            if (deleteResponse.isSuccessful()) {
                LOGGER.debug("Deleted comment ID: {}", commentId);
            } else {
                LOGGER.debug("Failed to delete comment ID: {}", commentId);
            }
        }
    }
    
    /**
     * Update an existing comment's content.
     * Uses PUT request to Bitbucket API.
     */
    public void updateComment(String commentId, String newContent) throws IOException {
        String workspace = vcsRepoInfo.getRepoWorkspace();
        String repoSlug = vcsRepoInfo.getRepoSlug();

        ObjectMapper objectMapper = new ObjectMapper();
        BitbucketCommentContent commentContent = new BitbucketCommentContent(newContent);
        BitbucketSummarizeComment comment = createSummarizeComment(commentContent);

        String body = objectMapper.writeValueAsString(comment);
        String apiUrl = String.format(
            "https://api.bitbucket.org/2.0/repositories/%s/%s/pullrequests/%s/comments/%s",
            workspace, repoSlug, prNumber, commentId
        );

        Request request = new Request.Builder()
                .put(RequestBody.create(body, APPLICATION_JSON_MEDIA_TYPE))
                .url(apiUrl)
                .build();

        LOGGER.info("Updating comment {} on bitbucket cloud: {}", commentId, apiUrl);

        try (Response response = authorizedOkHttpClient.newCall(request).execute()) {
            validate(response);
            LOGGER.debug("Successfully updated comment ID: {}", commentId);
        }
    }
    
    /**
     * Delete comments containing a specific marker text.
     * @param marker The marker text to search for in comment content
     * @return Number of comments deleted
     */
    public int deleteCommentsByMarker(String marker) throws IOException {
        String workspace = vcsRepoInfo.getRepoWorkspace();
        String repoSlug = vcsRepoInfo.getRepoSlug();
        int deletedCount = 0;

        String apiUrl = String.format("https://api.bitbucket.org/2.0/repositories/%s/%s/pullrequests/%s/comments", workspace, repoSlug, prNumber);

        while (apiUrl != null) {
            Request request = new Request.Builder()
                    .get()
                    .url(apiUrl)
                    .build();

            try (Response response = authorizedOkHttpClient.newCall(request).execute()) {
                if (!response.isSuccessful()) {
                    throw new IOException("Failed to fetch comments: " + response.code());
                }
                ObjectMapper objectMapper = new ObjectMapper();
                JsonNode root = objectMapper.readTree(response.body().string());
                JsonNode comments = root.get("values");

                if (comments != null && comments.isArray()) {
                    for (JsonNode comment : comments) {
                        JsonNode content = comment.path("content").path("raw");
                        String contentText = content.asText();

                        if (contentText.contains(marker)) {
                            String commentId = comment.get("id").asText();
                            deleteCommentById(commentId);
                            deletedCount++;
                        }
                    }
                }

                JsonNode nextNode = root.get("next");
                apiUrl = nextNode != null && !nextNode.isNull() ? nextNode.asText() : null;
            }
        }
        
        LOGGER.info("Deleted {} comments with marker: {}", deletedCount, marker);
        return deletedCount;
    }

    private void deleteOldSummarizeComments() throws IOException {
        String workspace = vcsRepoInfo.getRepoWorkspace();
        String repoSlug = vcsRepoInfo.getRepoSlug();

        String apiUrl = String.format("https://api.bitbucket.org/2.0/repositories/%s/%s/pullrequests/%s/comments", workspace, repoSlug, prNumber);

        while (apiUrl != null) {
            Request request = new Request.Builder()
                    .get()
                    .url(apiUrl)
                    .build();

            try (Response response = authorizedOkHttpClient.newCall(request).execute()) {
                if (!response.isSuccessful()) {
                    throw new IOException("Failed to fetch comments: " + response.code());
                }
                ObjectMapper objectMapper = new ObjectMapper();
                JsonNode root = objectMapper.readTree(response.body().string());
                JsonNode comments = root.get("values");

                if (comments != null && comments.isArray()) {
                    for (JsonNode comment : comments) {
                        deleteComment(comment);
                    }
                }

                JsonNode nextNode = root.get("next");
                apiUrl = nextNode != null ? nextNode.asText() : null;
            }
        }
    }

    private BitbucketSummarizeComment createSummarizeComment(BitbucketCommentContent commentData) {
        return new BitbucketSummarizeComment(commentData);
    }

    private String validate(Response response) throws IOException {
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
            return response.body().string();
        }
    }
}
