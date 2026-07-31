package org.rostilos.codecrow.vcsclient.gitlab.api;

import com.fasterxml.jackson.databind.JsonNode;
import okhttp3.Response;
import org.slf4j.Logger;
import org.slf4j.LoggerFactory;

import java.io.IOException;
import java.util.ArrayList;
import java.util.List;

/**
 * GitLab diff endpoints and unified-diff conversion.
 */
public final class GitLabDiffApi {

    private static final Logger log = LoggerFactory.getLogger(GitLabDiffApi.class);
    private static final int MAX_PAGE_SIZE = 100;

    private final GitLabApiContext api;

    public GitLabDiffApi(GitLabApiContext api) {
        this.api = api;
    }

    public String getMergeRequestDiff(
            String namespace,
            String project,
            long mergeRequestIid
    ) throws IOException {
        List<JsonNode> diffs = new ArrayList<>();
        int page = 1;

        while (true) {
            String url = api.projectUrl(namespace, project)
                    + "/merge_requests/" + mergeRequestIid
                    + "/diffs?page=" + page + "&per_page=" + MAX_PAGE_SIZE;
            try (Response response = api.execute(api.get(url))) {
                if (!response.isSuccessful()) {
                    throw api.error("get merge request diff", response);
                }

                JsonNode pageDiffs = api.objectMapper().readTree(
                        api.bodyOr(response, "[]"));
                if (!pageDiffs.isArray() || pageDiffs.isEmpty()) {
                    break;
                }
                pageDiffs.forEach(diffs::add);

                String totalPages = response.header("X-Total-Pages");
                if (totalPages != null && !totalPages.isBlank()) {
                    if (page >= Integer.parseInt(totalPages)) {
                        break;
                    }
                } else if (pageDiffs.size() < MAX_PAGE_SIZE) {
                    break;
                }
                page++;
            }
        }

        log.debug("Fetched {} diffs for MR {}", diffs.size(), mergeRequestIid);
        return buildUnifiedDiff(diffs);
    }

    public String getCommitDiff(
            String namespace,
            String project,
            String commitSha
    ) throws IOException {
        String url = api.projectUrl(namespace, project)
                + "/repository/commits/" + api.encode(commitSha) + "/diff";
        try (Response response = api.execute(api.get(url))) {
            if (!response.isSuccessful()) {
                throw api.error("get commit diff", response);
            }
            JsonNode diffs = api.objectMapper().readTree(api.bodyOr(response, "[]"));
            return buildUnifiedDiff(arrayElements(diffs));
        }
    }

    public String getCommitRangeDiff(
            String namespace,
            String project,
            String baseCommitSha,
            String headCommitSha
    ) throws IOException {
        String url = api.projectUrl(namespace, project)
                + "/repository/compare?from=" + api.encode(baseCommitSha)
                + "&to=" + api.encode(headCommitSha);
        try (Response response = api.execute(api.get(url))) {
            if (!response.isSuccessful()) {
                throw api.error("get commit range diff", response);
            }
            JsonNode root = api.objectMapper().readTree(api.bodyOr(response, "{}"));
            return buildUnifiedDiff(arrayElements(root.path("diffs")));
        }
    }

    private List<JsonNode> arrayElements(JsonNode value) {
        if (value == null || !value.isArray()) {
            return List.of();
        }
        List<JsonNode> elements = new ArrayList<>(value.size());
        value.forEach(elements::add);
        return elements;
    }

    private String buildUnifiedDiff(List<JsonNode> diffs) {
        StringBuilder combinedDiff = new StringBuilder();
        for (JsonNode diffEntry : diffs) {
            String oldPath = diffEntry.path("old_path").asText("");
            String newPath = diffEntry.path("new_path").asText("");
            String diff = diffEntry.path("diff").asText("");
            boolean newFile = diffEntry.path("new_file").asBoolean(false);
            boolean deletedFile = diffEntry.path("deleted_file").asBoolean(false);
            boolean renamedFile = diffEntry.path("renamed_file").asBoolean(false);

            combinedDiff.append("diff --git a/")
                    .append(oldPath)
                    .append(" b/")
                    .append(newPath)
                    .append("\n");
            if (newFile) {
                combinedDiff.append("new file mode 100644\n")
                        .append("--- /dev/null\n")
                        .append("+++ b/").append(newPath).append("\n");
            } else if (deletedFile) {
                combinedDiff.append("deleted file mode 100644\n")
                        .append("--- a/").append(oldPath).append("\n")
                        .append("+++ /dev/null\n");
            } else {
                if (renamedFile) {
                    combinedDiff.append("rename from ").append(oldPath).append("\n")
                            .append("rename to ").append(newPath).append("\n");
                }
                combinedDiff.append("--- a/").append(oldPath).append("\n")
                        .append("+++ b/").append(newPath).append("\n");
            }

            if (!diff.isEmpty()) {
                combinedDiff.append(diff);
                if (!diff.endsWith("\n")) {
                    combinedDiff.append("\n");
                }
            }
            combinedDiff.append("\n");
        }
        return combinedDiff.toString();
    }
}
