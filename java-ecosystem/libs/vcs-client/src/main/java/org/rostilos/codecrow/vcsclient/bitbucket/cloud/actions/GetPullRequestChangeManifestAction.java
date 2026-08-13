package org.rostilos.codecrow.vcsclient.bitbucket.cloud.actions;

import com.fasterxml.jackson.databind.JsonNode;
import com.fasterxml.jackson.databind.ObjectMapper;
import okhttp3.OkHttpClient;
import okhttp3.Request;
import okhttp3.Response;
import org.rostilos.codecrow.vcsclient.bitbucket.cloud.BitbucketCloudConfig;
import org.rostilos.codecrow.vcsclient.model.VcsPullRequestChangeManifest;

import java.io.IOException;
import java.util.ArrayList;
import java.util.List;

/** Retrieves Bitbucket Cloud's paginated, structured PR diffstat inventory. */
public final class GetPullRequestChangeManifestAction {

    private static final int PAGE_LENGTH = 100;

    private final OkHttpClient httpClient;
    private final ObjectMapper objectMapper = new ObjectMapper();

    public GetPullRequestChangeManifestAction(OkHttpClient httpClient) {
        this.httpClient = httpClient;
    }

    public VcsPullRequestChangeManifest getPullRequestChangeManifest(
            String workspace,
            String repoSlug,
            long pullRequestNumber
    ) throws IOException {
        List<VcsPullRequestChangeManifest.Change> changes = new ArrayList<>();
        String url = String.format(
                "%s/repositories/%s/%s/pullrequests/%d/diffstat?pagelen=%d",
                BitbucketCloudConfig.BITBUCKET_API_BASE,
                workspace != null ? workspace : "",
                repoSlug,
                pullRequestNumber,
                PAGE_LENGTH);
        boolean complete = true;
        Integer expectedCount = null;
        int pages = 0;

        while (url != null) {
            Request request = new Request.Builder()
                    .url(url)
                    .header("Accept", "application/json")
                    .get()
                    .build();
            try (Response response = httpClient.newCall(request).execute()) {
                pages++;
                if (!response.isSuccessful()) {
                    String body = response.body() != null ? response.body().string() : "";
                    throw new IOException("Bitbucket diffstat returned "
                            + response.code() + ": " + body);
                }
                JsonNode root = objectMapper.readTree(
                        response.body() != null ? response.body().string() : "{}");
                JsonNode values = root.path("values");
                if (!values.isArray()) {
                    complete = false;
                    break;
                }
                if (root.has("size") && root.path("size").canConvertToInt()) {
                    int pageExpectedCount = root.path("size").asInt();
                    if (expectedCount != null && expectedCount != pageExpectedCount) {
                        complete = false;
                    }
                    expectedCount = pageExpectedCount;
                }

                for (JsonNode entry : values) {
                    String status = entry.path("status").asText("");
                    String oldPath = entry.path("old").path("path").asText("");
                    String newPath = entry.path("new").path("path").asText("");
                    VcsPullRequestChangeManifest.ChangeKind kind = changeKind(status);
                    String path = kind == VcsPullRequestChangeManifest.ChangeKind.DELETED
                            ? oldPath : newPath;
                    if (path.isBlank()) {
                        complete = false;
                        continue;
                    }
                    changes.add(new VcsPullRequestChangeManifest.Change(
                            path,
                            kind == VcsPullRequestChangeManifest.ChangeKind.RENAMED
                                    ? oldPath : "",
                            kind));
                }
                url = root.hasNonNull("next") ? root.path("next").asText() : null;
            }
        }

        if (expectedCount == null || expectedCount != changes.size()) {
            complete = false;
        }
        String receipt = "bitbucket-cloud:pull-request-diffstat:pages=" + pages
                + ":entries=" + changes.size()
                + ":expected=" + (expectedCount != null ? expectedCount : "unknown");
        return new VcsPullRequestChangeManifest(
                changes,
                complete
                        ? VcsPullRequestChangeManifest.Completeness.COMPLETE
                        : VcsPullRequestChangeManifest.Completeness.INCOMPLETE,
                receipt);
    }

    private VcsPullRequestChangeManifest.ChangeKind changeKind(String status) {
        return switch (status != null ? status.toLowerCase() : "") {
            case "added" -> VcsPullRequestChangeManifest.ChangeKind.ADDED;
            case "removed" -> VcsPullRequestChangeManifest.ChangeKind.DELETED;
            case "renamed" -> VcsPullRequestChangeManifest.ChangeKind.RENAMED;
            case "modified" -> VcsPullRequestChangeManifest.ChangeKind.MODIFIED;
            default -> VcsPullRequestChangeManifest.ChangeKind.UNKNOWN;
        };
    }
}
