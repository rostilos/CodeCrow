package org.rostilos.codecrow.vcsclient.gitlab.api;

import com.fasterxml.jackson.databind.JsonNode;
import okhttp3.Response;
import org.rostilos.codecrow.vcsclient.model.VcsPullRequestChangeManifest;
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
        return buildUnifiedDiff(fetchMergeRequestDiffs(
                namespace, project, mergeRequestIid).diffs());
    }

    public VcsPullRequestChangeManifest getMergeRequestChangeManifest(
            String namespace,
            String project,
            long mergeRequestIid
    ) throws IOException {
        ExpectedChangeCount expected = fetchExpectedChangeCount(
                namespace, project, mergeRequestIid);
        MergeRequestDiffResult result = fetchMergeRequestDiffs(
                namespace, project, mergeRequestIid);
        List<VcsPullRequestChangeManifest.Change> changes = new ArrayList<>();
        boolean complete = result.paginationComplete()
                && expected.available()
                && !expected.truncated();

        for (JsonNode diffEntry : result.diffs()) {
            String oldPath = diffEntry.path("old_path").asText("");
            String newPath = diffEntry.path("new_path").asText("");
            boolean deleted = diffEntry.path("deleted_file").asBoolean(false);
            String path = deleted ? oldPath : newPath;
            if (path.isBlank()) {
                complete = false;
                continue;
            }
            VcsPullRequestChangeManifest.ChangeKind kind = changeKind(diffEntry);
            changes.add(new VcsPullRequestChangeManifest.Change(
                    path,
                    kind == VcsPullRequestChangeManifest.ChangeKind.RENAMED ? oldPath : "",
                    kind));
        }
        if (expected.available() && expected.count() != changes.size()) {
            complete = false;
        }

        String receipt = "gitlab:merge-request-diffs:pages=" + result.pages()
                + ":entries=" + changes.size()
                + ":expected=" + (expected.available() ? expected.rawValue() : "unknown");
        return new VcsPullRequestChangeManifest(
                changes,
                complete
                        ? VcsPullRequestChangeManifest.Completeness.COMPLETE
                        : VcsPullRequestChangeManifest.Completeness.INCOMPLETE,
                receipt);
    }

    private MergeRequestDiffResult fetchMergeRequestDiffs(
            String namespace,
            String project,
            long mergeRequestIid
    ) throws IOException {
        List<JsonNode> diffs = new ArrayList<>();
        int page = 1;
        boolean paginationComplete = true;

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
                if (!pageDiffs.isArray()) {
                    paginationComplete = false;
                    break;
                }
                if (pageDiffs.isEmpty()) {
                    break;
                }
                pageDiffs.forEach(diffs::add);

                String totalPages = response.header("X-Total-Pages");
                if (totalPages != null && !totalPages.isBlank()) {
                    int parsedTotalPages;
                    try {
                        parsedTotalPages = Integer.parseInt(totalPages);
                    } catch (NumberFormatException invalidPagination) {
                        paginationComplete = false;
                        break;
                    }
                    if (page >= parsedTotalPages) {
                        break;
                    }
                } else if (pageDiffs.size() < MAX_PAGE_SIZE) {
                    break;
                }
                page++;
            }
        }

        log.debug("Fetched {} diffs for MR {}", diffs.size(), mergeRequestIid);
        return new MergeRequestDiffResult(
                List.copyOf(diffs), paginationComplete, page);
    }

    private ExpectedChangeCount fetchExpectedChangeCount(
            String namespace,
            String project,
            long mergeRequestIid
    ) throws IOException {
        String url = api.projectUrl(namespace, project)
                + "/merge_requests/" + mergeRequestIid;
        try (Response response = api.execute(api.get(url))) {
            if (!response.isSuccessful()) {
                throw api.error("get merge request change count", response);
            }
            JsonNode root = api.objectMapper().readTree(api.bodyOr(response, "{}"));
            String raw = root.path("changes_count").asText("").trim();
            if (raw.isBlank()) {
                return new ExpectedChangeCount(0, false, false, "unknown");
            }
            boolean truncated = raw.endsWith("+");
            String numeric = truncated ? raw.substring(0, raw.length() - 1) : raw;
            try {
                return new ExpectedChangeCount(
                        Integer.parseInt(numeric), true, truncated, raw);
            } catch (NumberFormatException invalidCount) {
                return new ExpectedChangeCount(0, false, false, raw);
            }
        }
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

    private VcsPullRequestChangeManifest.ChangeKind changeKind(JsonNode diffEntry) {
        if (diffEntry.path("new_file").asBoolean(false)) {
            return VcsPullRequestChangeManifest.ChangeKind.ADDED;
        }
        if (diffEntry.path("deleted_file").asBoolean(false)) {
            return VcsPullRequestChangeManifest.ChangeKind.DELETED;
        }
        if (diffEntry.path("renamed_file").asBoolean(false)) {
            return VcsPullRequestChangeManifest.ChangeKind.RENAMED;
        }
        return VcsPullRequestChangeManifest.ChangeKind.MODIFIED;
    }

    private record MergeRequestDiffResult(
            List<JsonNode> diffs,
            boolean paginationComplete,
            int pages) {}

    private record ExpectedChangeCount(
            int count,
            boolean available,
            boolean truncated,
            String rawValue) {}
}
