package org.rostilos.codecrow.vcsclient.gitlab.actions;

import com.fasterxml.jackson.core.type.TypeReference;
import com.fasterxml.jackson.databind.ObjectMapper;
import okhttp3.*;
import org.rostilos.codecrow.vcsclient.gitlab.GitLabConfig;
import org.slf4j.Logger;
import org.slf4j.LoggerFactory;

import java.io.IOException;
import java.net.URLEncoder;
import java.nio.charset.StandardCharsets;
import java.util.HashMap;
import java.util.List;
import java.util.Map;

/**
 * Action to comment on GitLab Merge Requests.
 */
public class CommentOnMergeRequestAction {

    private static final Logger log = LoggerFactory.getLogger(CommentOnMergeRequestAction.class);
    private static final MediaType JSON = MediaType.parse("application/json");
    private final OkHttpClient authorizedOkHttpClient;
    private final ObjectMapper objectMapper = new ObjectMapper();

    public CommentOnMergeRequestAction(OkHttpClient authorizedOkHttpClient) {
        this.authorizedOkHttpClient = authorizedOkHttpClient;
    }

    /**
     * Post a general comment on a merge request.
     */
    public void postComment(String namespace, String project, int mergeRequestIid, String body) throws IOException {
        String projectPath = namespace + "/" + project;
        String encodedPath = URLEncoder.encode(projectPath, StandardCharsets.UTF_8);
        String apiUrl = String.format("%s/projects/%s/merge_requests/%d/notes",
                GitLabConfig.API_BASE, encodedPath, mergeRequestIid);

        Map<String, String> payload = new HashMap<>();
        payload.put("body", body);

        Request req = new Request.Builder()
                .url(apiUrl)
                .header("Accept", "application/json")
                .post(RequestBody.create(objectMapper.writeValueAsString(payload), JSON))
                .build();

        try (Response resp = authorizedOkHttpClient.newCall(req).execute()) {
            if (!resp.isSuccessful()) {
                String respBody = resp.body() != null ? resp.body().string() : "";
                String msg = String.format("GitLab returned non-success response %d for URL %s: %s",
                        resp.code(), apiUrl, respBody);
                log.warn(msg);
                throw new IOException(msg);
            }
        }
    }

    /**
     * Post an inline comment on a specific file and line in a merge request.
     */
    public void postLineComment(String namespace, String project, int mergeRequestIid,
                                String body, String baseSha, String headSha, String startSha,
                                String filePath, int newLine) throws IOException {
        String projectPath = namespace + "/" + project;
        String encodedPath = URLEncoder.encode(projectPath, StandardCharsets.UTF_8);
        String apiUrl = String.format("%s/projects/%s/merge_requests/%d/discussions",
                GitLabConfig.API_BASE, encodedPath, mergeRequestIid);

        Map<String, Object> position = new HashMap<>();
        position.put("base_sha", baseSha);
        position.put("head_sha", headSha);
        position.put("start_sha", startSha);
        position.put("position_type", "text");
        position.put("new_path", filePath);
        position.put("new_line", newLine);

        Map<String, Object> payload = new HashMap<>();
        payload.put("body", body);
        payload.put("position", position);

        Request req = new Request.Builder()
                .url(apiUrl)
                .header("Accept", "application/json")
                .post(RequestBody.create(objectMapper.writeValueAsString(payload), JSON))
                .build();

        try (Response resp = authorizedOkHttpClient.newCall(req).execute()) {
            if (!resp.isSuccessful()) {
                String respBody = resp.body() != null ? resp.body().string() : "";
                log.warn("Failed to post line comment: {} - {}", resp.code(), respBody);
            }
        }
    }

    /**
     * List all notes (comments) on a merge request.
     */
    public List<Map<String, Object>> listNotes(String namespace, String project, int mergeRequestIid) throws IOException {
        String projectPath = namespace + "/" + project;
        String encodedPath = URLEncoder.encode(projectPath, StandardCharsets.UTF_8);
        String apiUrl = String.format("%s/projects/%s/merge_requests/%d/notes?per_page=100",
                GitLabConfig.API_BASE, encodedPath, mergeRequestIid);

        Request req = new Request.Builder()
                .url(apiUrl)
                .header("Accept", "application/json")
                .get()
                .build();

        try (Response resp = authorizedOkHttpClient.newCall(req).execute()) {
            if (!resp.isSuccessful()) {
                String respBody = resp.body() != null ? resp.body().string() : "";
                log.warn("Failed to list notes: {} - {}", resp.code(), respBody);
                return List.of();
            }
            String body = resp.body() != null ? resp.body().string() : "[]";
            return objectMapper.readValue(body, new TypeReference<List<Map<String, Object>>>() {});
        }
    }

    /**
     * Update an existing note on a merge request.
     */
    public void updateNote(String namespace, String project, int mergeRequestIid, long noteId, String body) throws IOException {
        String projectPath = namespace + "/" + project;
        String encodedPath = URLEncoder.encode(projectPath, StandardCharsets.UTF_8);
        String apiUrl = String.format("%s/projects/%s/merge_requests/%d/notes/%d",
                GitLabConfig.API_BASE, encodedPath, mergeRequestIid, noteId);

        Map<String, String> payload = new HashMap<>();
        payload.put("body", body);

        Request req = new Request.Builder()
                .url(apiUrl)
                .header("Accept", "application/json")
                .put(RequestBody.create(objectMapper.writeValueAsString(payload), JSON))
                .build();

        try (Response resp = authorizedOkHttpClient.newCall(req).execute()) {
            if (!resp.isSuccessful()) {
                String respBody = resp.body() != null ? resp.body().string() : "";
                log.warn("Failed to update note: {} - {}", resp.code(), respBody);
                throw new IOException("Failed to update note: " + resp.code());
            }
        }
    }

    /**
     * Delete a note from a merge request.
     */
    public void deleteNote(String namespace, String project, int mergeRequestIid, long noteId) throws IOException {
        String projectPath = namespace + "/" + project;
        String encodedPath = URLEncoder.encode(projectPath, StandardCharsets.UTF_8);
        String apiUrl = String.format("%s/projects/%s/merge_requests/%d/notes/%d",
                GitLabConfig.API_BASE, encodedPath, mergeRequestIid, noteId);

        Request req = new Request.Builder()
                .url(apiUrl)
                .header("Accept", "application/json")
                .delete()
                .build();

        try (Response resp = authorizedOkHttpClient.newCall(req).execute()) {
            if (!resp.isSuccessful() && resp.code() != 404) {
                String respBody = resp.body() != null ? resp.body().string() : "";
                log.warn("Failed to delete note: {} - {}", resp.code(), respBody);
            }
        }
    }

    /**
     * Find an existing comment by marker.
     */
    public Long findCommentByMarker(String namespace, String project, int mergeRequestIid, String marker) throws IOException {
        List<Map<String, Object>> notes = listNotes(namespace, project, mergeRequestIid);
        for (Map<String, Object> note : notes) {
            Object bodyObj = note.get("body");
            if (bodyObj != null && bodyObj.toString().contains(marker)) {
                Object idObj = note.get("id");
                if (idObj instanceof Number) {
                    return ((Number) idObj).longValue();
                }
            }
        }
        return null;
    }
}
