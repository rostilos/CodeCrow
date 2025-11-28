package org.rostilos.codecrow.vcsclient.bitbucket.cloud.actions;

import com.fasterxml.jackson.databind.JsonNode;
import com.fasterxml.jackson.databind.ObjectMapper;
import okhttp3.*;
import org.rostilos.codecrow.core.model.project.ProjectVcsConnectionBinding;
import org.rostilos.codecrow.vcsclient.bitbucket.model.comment.BitbucketCommentContent;
import org.rostilos.codecrow.vcsclient.bitbucket.model.comment.BitbucketSummarizeComment;
import org.slf4j.Logger;
import org.slf4j.LoggerFactory;

import java.io.IOException;
import java.util.Optional;

//TODO: bstract or a single client class for actions
public class CommentOnBitbucketCloudAction {
    private final OkHttpClient authorizedOkHttpClient;
    private final ProjectVcsConnectionBinding projectVcsConnectionBinding;
    private final Long prNumber;

    private static final MediaType APPLICATION_JSON_MEDIA_TYPE = MediaType.get("application/json");
    private static final Logger LOGGER = LoggerFactory.getLogger(CommentOnBitbucketCloudAction.class);
    private static final String AI_SUMMARIZE_MARKER = "[Codecrow AI Summarize]";

    public CommentOnBitbucketCloudAction(
            OkHttpClient authorizedOkHttpClient,
            ProjectVcsConnectionBinding projectVcsConnectionBinding,
            Long prNumber
    ) {
        this.authorizedOkHttpClient = authorizedOkHttpClient;
        this.projectVcsConnectionBinding = projectVcsConnectionBinding;
        this.prNumber = prNumber;
    }

    public void postSummaryResult(String textContent) throws IOException {
        String workspace = projectVcsConnectionBinding.getWorkspace();
        String repoSlug = projectVcsConnectionBinding.getRepoSlug();

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

    private void deleteOldSummarizeComments() throws IOException {
        String workspace = projectVcsConnectionBinding.getWorkspace();
        String repoSlug = projectVcsConnectionBinding.getRepoSlug();

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
