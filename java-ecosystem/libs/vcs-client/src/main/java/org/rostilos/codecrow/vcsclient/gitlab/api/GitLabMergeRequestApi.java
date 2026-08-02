package org.rostilos.codecrow.vcsclient.gitlab.api;

import com.fasterxml.jackson.core.type.TypeReference;
import com.fasterxml.jackson.databind.JsonNode;
import okhttp3.Response;
import org.slf4j.Logger;
import org.slf4j.LoggerFactory;

import java.io.IOException;
import java.util.LinkedHashMap;
import java.util.List;
import java.util.Map;

/**
 * GitLab merge-request endpoints for one configured API context.
 */
public final class GitLabMergeRequestApi {

    private static final Logger log =
            LoggerFactory.getLogger(GitLabMergeRequestApi.class);

    private final GitLabApiContext api;

    public GitLabMergeRequestApi(GitLabApiContext api) {
        this.api = api;
    }

    public JsonNode get(String namespace, String project, long mergeRequestIid)
            throws IOException {
        return api.executeJson(
                "get merge request",
                api.get(mergeRequestUrl(namespace, project, mergeRequestIid)));
    }

    public JsonNode list(String namespace, String project, String state, int limit)
            throws IOException {
        String normalizedState = switch (state == null ? "open" : state.toLowerCase()) {
            case "open", "opened" -> "opened";
            case "merged" -> "merged";
            case "closed", "declined" -> "closed";
            default -> "all";
        };
        String url = mergeRequestsUrl(namespace, project)
                + "?state=" + normalizedState
                + "&per_page=" + Math.min(Math.max(limit, 1), 100);
        return api.executeJson("list merge requests", api.get(url));
    }

    public JsonNode create(
            String namespace,
            String project,
            String title,
            String description,
            String sourceBranch,
            String targetBranch,
            List<String> reviewerIds
    ) throws IOException {
        Map<String, Object> body = new LinkedHashMap<>();
        body.put("title", title);
        body.put("description", description);
        body.put("source_branch", sourceBranch);
        body.put("target_branch", targetBranch);
        if (reviewerIds != null && !reviewerIds.isEmpty()) {
            body.put("reviewer_ids", reviewerIds);
        }
        return api.executeJson(
                "create merge request",
                api.postJson(
                        mergeRequestsUrl(namespace, project),
                        api.objectMapper().writeValueAsString(body)));
    }

    public JsonNode update(
            String namespace,
            String project,
            long mergeRequestIid,
            String title,
            String description
    ) throws IOException {
        Map<String, Object> body = new LinkedHashMap<>();
        if (title != null) {
            body.put("title", title);
        }
        if (description != null) {
            body.put("description", description);
        }
        return api.executeJson(
                "update merge request",
                api.putJson(
                        mergeRequestUrl(namespace, project, mergeRequestIid),
                        api.objectMapper().writeValueAsString(body)));
    }

    public JsonNode getActivity(String namespace, String project, long mergeRequestIid)
            throws IOException {
        return api.executeJson(
                "get merge request activity",
                api.get(mergeRequestUrl(namespace, project, mergeRequestIid)
                        + "/resource_state_events"));
    }

    public JsonNode approve(String namespace, String project, long mergeRequestIid)
            throws IOException {
        return api.executeJson(
                "approve merge request",
                api.postJson(
                        mergeRequestUrl(namespace, project, mergeRequestIid) + "/approve",
                        "{}"));
    }

    public JsonNode unapprove(String namespace, String project, long mergeRequestIid)
            throws IOException {
        return api.executeJson(
                "unapprove merge request",
                api.postJson(
                        mergeRequestUrl(namespace, project, mergeRequestIid) + "/unapprove",
                        "{}"));
    }

    public JsonNode close(String namespace, String project, long mergeRequestIid)
            throws IOException {
        return api.executeJson(
                "close merge request",
                api.putJson(
                        mergeRequestUrl(namespace, project, mergeRequestIid),
                        "{\"state_event\":\"close\"}"));
    }

    public JsonNode merge(
            String namespace,
            String project,
            long mergeRequestIid,
            String message,
            String strategy
    ) throws IOException {
        Map<String, Object> body = new LinkedHashMap<>();
        if (message != null) {
            body.put("merge_commit_message", message);
        }
        if ("squash".equalsIgnoreCase(strategy)) {
            body.put("squash", true);
        }
        return api.executeJson(
                "merge merge request",
                api.putJson(
                        mergeRequestUrl(namespace, project, mergeRequestIid) + "/merge",
                        api.objectMapper().writeValueAsString(body)));
    }

    public JsonNode getNotes(String namespace, String project, long mergeRequestIid)
            throws IOException {
        return api.executeJson(
                "get merge request notes",
                api.get(notesUrl(namespace, project, mergeRequestIid)
                        + "?per_page=100"));
    }

    public JsonNode getDiscussion(
            String namespace,
            String project,
            long mergeRequestIid,
            String discussionId
    ) throws IOException {
        return api.executeJson(
                "get merge request discussion",
                api.get(discussionUrl(namespace, project, mergeRequestIid, discussionId)));
    }

    public JsonNode postDiscussionReply(
            String namespace,
            String project,
            long mergeRequestIid,
            String discussionId,
            String body
    ) throws IOException {
        return api.executeJson(
                "reply to merge request discussion",
                api.postJson(
                        discussionUrl(namespace, project, mergeRequestIid, discussionId) + "/notes",
                        api.objectMapper().writeValueAsString(Map.of("body", body))));
    }

    public JsonNode getCommits(String namespace, String project, long mergeRequestIid)
            throws IOException {
        return api.executeJson(
                "get merge request commits",
                api.get(mergeRequestUrl(namespace, project, mergeRequestIid)
                        + "/commits"));
    }

    public Long findForCommit(String namespace, String project, String commitHash) {
        String url = api.projectUrl(namespace, project)
                + "/repository/commits/" + api.encode(commitHash)
                + "/merge_requests";
        try {
            JsonNode mergeRequests = api.executeJson(
                    "find merge request for commit",
                    api.get(url));
            if (!mergeRequests.isArray() || mergeRequests.isEmpty()) {
                return null;
            }
            for (JsonNode mergeRequest : mergeRequests) {
                if ("merged".equalsIgnoreCase(mergeRequest.path("state").asText())) {
                    return mergeRequest.path("iid").asLong();
                }
            }
            return mergeRequests.get(0).path("iid").asLong();
        } catch (Exception error) {
            log.warn("Error finding GitLab MR for commit {}: {}",
                    commitHash, error.getMessage());
            return null;
        }
    }

    public void postComment(
            String namespace,
            String project,
            long mergeRequestIid,
            String body
    ) throws IOException {
        Map<String, String> payload = new LinkedHashMap<>();
        payload.put("body", body);
        api.executeSuccessfully(
                "post merge request comment",
                api.postJson(
                        notesUrl(namespace, project, mergeRequestIid),
                        api.objectMapper().writeValueAsString(payload)));
    }

    public void postLineComment(
            String namespace,
            String project,
            long mergeRequestIid,
            String body,
            String baseSha,
            String headSha,
            String startSha,
            String filePath,
            int newLine
    ) throws IOException {
        Map<String, Object> position = new LinkedHashMap<>();
        position.put("base_sha", baseSha);
        position.put("head_sha", headSha);
        position.put("start_sha", startSha);
        position.put("position_type", "text");
        position.put("new_path", filePath);
        position.put("new_line", newLine);

        Map<String, Object> payload = new LinkedHashMap<>();
        payload.put("body", body);
        payload.put("position", position);

        String url = mergeRequestUrl(namespace, project, mergeRequestIid)
                + "/discussions";
        try (Response response = api.execute(api.postJson(
                url,
                api.objectMapper().writeValueAsString(payload)))) {
            if (!response.isSuccessful()) {
                log.warn("Failed to post GitLab line comment: HTTP {} - {}",
                        response.code(), api.bodyOr(response, ""));
            }
        }
    }

    public List<Map<String, Object>> listNotes(
            String namespace,
            String project,
            long mergeRequestIid
    ) throws IOException {
        String url = notesUrl(namespace, project, mergeRequestIid)
                + "?per_page=100";
        try (Response response = api.execute(api.get(url))) {
            if (!response.isSuccessful()) {
                log.warn("Failed to list GitLab MR notes: HTTP {} - {}",
                        response.code(), api.bodyOr(response, ""));
                return List.of();
            }
            return api.objectMapper().readValue(
                    api.bodyOr(response, "[]"),
                    new TypeReference<List<Map<String, Object>>>() {});
        }
    }

    public void updateNote(
            String namespace,
            String project,
            long mergeRequestIid,
            long noteId,
            String body
    ) throws IOException {
        Map<String, String> payload = new LinkedHashMap<>();
        payload.put("body", body);
        api.executeSuccessfully(
                "update merge request note",
                api.putJson(
                        notesUrl(namespace, project, mergeRequestIid) + "/" + noteId,
                        api.objectMapper().writeValueAsString(payload)));
    }

    public void deleteNote(
            String namespace,
            String project,
            long mergeRequestIid,
            long noteId
    ) throws IOException {
        try (Response response = api.execute(api.delete(
                notesUrl(namespace, project, mergeRequestIid) + "/" + noteId))) {
            if (!response.isSuccessful() && response.code() != 404) {
                log.warn("Failed to delete GitLab MR note: HTTP {} - {}",
                        response.code(), api.bodyOr(response, ""));
            }
        }
    }

    public Long findNoteByMarker(
            String namespace,
            String project,
            long mergeRequestIid,
            String marker
    ) throws IOException {
        for (Map<String, Object> note : listNotes(
                namespace, project, mergeRequestIid)) {
            Object body = note.get("body");
            Object id = note.get("id");
            if (body != null
                    && body.toString().contains(marker)
                    && id instanceof Number noteId) {
                return noteId.longValue();
            }
        }
        return null;
    }

    private String mergeRequestsUrl(String namespace, String project) {
        return api.projectUrl(namespace, project) + "/merge_requests";
    }

    private String mergeRequestUrl(
            String namespace,
            String project,
            long mergeRequestIid
    ) {
        return mergeRequestsUrl(namespace, project) + "/" + mergeRequestIid;
    }

    private String notesUrl(
            String namespace,
            String project,
            long mergeRequestIid
    ) {
        return mergeRequestUrl(namespace, project, mergeRequestIid) + "/notes";
    }

    private String discussionUrl(
            String namespace,
            String project,
            long mergeRequestIid,
            String discussionId
    ) {
        return mergeRequestUrl(namespace, project, mergeRequestIid)
                + "/discussions/" + api.encode(discussionId);
    }
}
