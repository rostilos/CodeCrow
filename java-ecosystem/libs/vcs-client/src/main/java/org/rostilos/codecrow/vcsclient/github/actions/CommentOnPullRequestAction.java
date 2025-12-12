package org.rostilos.codecrow.vcsclient.github.actions;

import com.fasterxml.jackson.core.type.TypeReference;
import com.fasterxml.jackson.databind.ObjectMapper;
import okhttp3.*;
import org.rostilos.codecrow.vcsclient.github.GitHubConfig;
import org.slf4j.Logger;
import org.slf4j.LoggerFactory;

import java.io.IOException;
import java.util.HashMap;
import java.util.List;
import java.util.Map;

public class CommentOnPullRequestAction {

    private static final Logger log = LoggerFactory.getLogger(CommentOnPullRequestAction.class);
    private static final MediaType JSON = MediaType.parse("application/json");
    private final OkHttpClient authorizedOkHttpClient;
    private final ObjectMapper objectMapper = new ObjectMapper();

    public CommentOnPullRequestAction(OkHttpClient authorizedOkHttpClient) {
        this.authorizedOkHttpClient = authorizedOkHttpClient;
    }

    public void postComment(String owner, String repo, int pullRequestNumber, String body) throws IOException {
        String apiUrl = String.format("%s/repos/%s/%s/issues/%d/comments",
                GitHubConfig.API_BASE, owner, repo, pullRequestNumber);

        Map<String, String> payload = new HashMap<>();
        payload.put("body", body);

        Request req = new Request.Builder()
                .url(apiUrl)
                .header("Accept", "application/vnd.github+json")
                .header("X-GitHub-Api-Version", "2022-11-28")
                .post(RequestBody.create(objectMapper.writeValueAsString(payload), JSON))
                .build();

        try (Response resp = authorizedOkHttpClient.newCall(req).execute()) {
            if (!resp.isSuccessful()) {
                String respBody = resp.body() != null ? resp.body().string() : "";
                String msg = String.format("GitHub returned non-success response %d for URL %s: %s",
                        resp.code(), apiUrl, respBody);
                log.warn(msg);
                throw new IOException(msg);
            }
        }
    }

    public void postReviewComment(String owner, String repo, int pullRequestNumber, 
                                   String body, String commitId, String path, int line) throws IOException {
        String apiUrl = String.format("%s/repos/%s/%s/pulls/%d/comments",
                GitHubConfig.API_BASE, owner, repo, pullRequestNumber);

        Map<String, Object> payload = new HashMap<>();
        payload.put("body", body);
        payload.put("commit_id", commitId);
        payload.put("path", path);
        payload.put("line", line);
        payload.put("side", "RIGHT");

        Request req = new Request.Builder()
                .url(apiUrl)
                .header("Accept", "application/vnd.github+json")
                .header("X-GitHub-Api-Version", "2022-11-28")
                .post(RequestBody.create(objectMapper.writeValueAsString(payload), JSON))
                .build();

        try (Response resp = authorizedOkHttpClient.newCall(req).execute()) {
            if (!resp.isSuccessful()) {
                String respBody = resp.body() != null ? resp.body().string() : "";
                log.warn("Failed to post review comment: {} - {}", resp.code(), respBody);
            }
        }
    }

    public List<Map<String, Object>> listComments(String owner, String repo, int prNumber) throws IOException {
        String apiUrl = String.format("%s/repos/%s/%s/issues/%d/comments?per_page=100",
                GitHubConfig.API_BASE, owner, repo, prNumber);

        Request req = new Request.Builder()
                .url(apiUrl)
                .header("Accept", "application/vnd.github+json")
                .header("X-GitHub-Api-Version", "2022-11-28")
                .get()
                .build();

        try (Response resp = authorizedOkHttpClient.newCall(req).execute()) {
            if (!resp.isSuccessful()) {
                String respBody = resp.body() != null ? resp.body().string() : "";
                log.warn("Failed to list comments: {} - {}", resp.code(), respBody);
                return List.of();
            }
            String body = resp.body() != null ? resp.body().string() : "[]";
            return objectMapper.readValue(body, new TypeReference<List<Map<String, Object>>>() {});
        }
    }

    public void deleteComment(String owner, String repo, long commentId) throws IOException {
        String apiUrl = String.format("%s/repos/%s/%s/issues/comments/%d",
                GitHubConfig.API_BASE, owner, repo, commentId);

        Request req = new Request.Builder()
                .url(apiUrl)
                .header("Accept", "application/vnd.github+json")
                .header("X-GitHub-Api-Version", "2022-11-28")
                .delete()
                .build();

        try (Response resp = authorizedOkHttpClient.newCall(req).execute()) {
            if (!resp.isSuccessful()) {
                String respBody = resp.body() != null ? resp.body().string() : "";
                log.warn("Failed to delete comment {}: {} - {}", commentId, resp.code(), respBody);
            } else {
                log.debug("Deleted comment {}", commentId);
            }
        }
    }

    public void deletePreviousComments(String owner, String repo, int prNumber, String markerText) throws IOException {
        List<Map<String, Object>> comments = listComments(owner, repo, prNumber);
        
        for (Map<String, Object> comment : comments) {
            String body = (String) comment.get("body");
            if (body != null && body.contains(markerText)) {
                Number id = (Number) comment.get("id");
                if (id != null) {
                    deleteComment(owner, repo, id.longValue());
                }
            }
        }
    }
}
