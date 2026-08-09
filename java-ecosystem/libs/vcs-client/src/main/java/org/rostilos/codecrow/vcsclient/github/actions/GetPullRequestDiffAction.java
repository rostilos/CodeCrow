package org.rostilos.codecrow.vcsclient.github.actions;

import com.fasterxml.jackson.databind.JsonNode;
import com.fasterxml.jackson.databind.ObjectMapper;
import okhttp3.OkHttpClient;
import okhttp3.Request;
import okhttp3.Response;
import org.rostilos.codecrow.vcsclient.github.GitHubConfig;
import org.slf4j.Logger;
import org.slf4j.LoggerFactory;

import java.io.IOException;
import java.util.ArrayList;
import java.util.List;
import java.util.regex.Matcher;
import java.util.regex.Pattern;

public class GetPullRequestDiffAction {

    private static final Logger log = LoggerFactory.getLogger(GetPullRequestDiffAction.class);
    private static final ObjectMapper objectMapper = new ObjectMapper();
    private static final Pattern LINK_NEXT_PATTERN = Pattern.compile("<([^>]+)>;\\s*rel=\"next\"");
    private final OkHttpClient authorizedOkHttpClient;

    public GetPullRequestDiffAction(OkHttpClient authorizedOkHttpClient) {
        this.authorizedOkHttpClient = authorizedOkHttpClient;
    }

    public String getPullRequestDiff(String owner, String repo, int pullRequestNumber) throws IOException {
        String apiUrl = String.format("%s/repos/%s/%s/pulls/%d",
                GitHubConfig.API_BASE, owner, repo, pullRequestNumber);

        Request req = new Request.Builder()
                .url(apiUrl)
                .header("Accept", "application/vnd.github.v3.diff")
                .header("X-GitHub-Api-Version", "2022-11-28")
                .get()
                .build();

        try (Response resp = authorizedOkHttpClient.newCall(req).execute()) {
            if (resp.code() == 406) {
                log.warn("GitHub returned 406 for diff endpoint (likely too large), falling back to /files endpoint for PR #{}", pullRequestNumber);
                return getPullRequestDiffFromFiles(owner, repo, pullRequestNumber);
            }
            if (!resp.isSuccessful()) {
                String body = resp.body() != null ? resp.body().string() : "";
                String msg = String.format("GitHub returned non-success response %d for URL %s: %s",
                        resp.code(), apiUrl, body);
                log.warn(msg);
                throw new IOException(msg);
            }
            return resp.body() != null ? resp.body().string() : "";
        }
    }

    /**
     * Fallback method when the diff endpoint returns 406 (diff too large).
     * Uses the /files endpoint which returns patches for each file with pagination.
     */
    private String getPullRequestDiffFromFiles(String owner, String repo, int pullRequestNumber) throws IOException {
        StringBuilder combinedDiff = new StringBuilder();
        for (PullRequestFilePatch file : getPullRequestFilePatches(
                owner, repo, pullRequestNumber)) {
            if (file.patch().isEmpty()) {
                continue;
            }

            // Build a unified diff header
            String fromFile = "renamed".equals(file.status())
                    && !file.previousFilename().isEmpty()
                    ? file.previousFilename()
                    : file.filename();
            combinedDiff.append("diff --git a/").append(fromFile)
                    .append(" b/").append(file.filename()).append("\n");

            if ("added".equals(file.status())) {
                combinedDiff.append("new file mode 100644\n");
            } else if ("removed".equals(file.status())) {
                combinedDiff.append("deleted file mode 100644\n");
            } else if ("renamed".equals(file.status())) {
                combinedDiff.append("rename from ").append(file.previousFilename()).append("\n");
                combinedDiff.append("rename to ").append(file.filename()).append("\n");
            }

            combinedDiff.append("--- a/").append(fromFile).append("\n");
            combinedDiff.append("+++ b/").append(file.filename()).append("\n");
            combinedDiff.append(file.patch()).append("\n");
        }
        return combinedDiff.toString();
    }

    /**
     * Return GitHub's structured per-file patches for a pull request.
     *
     * <p>The file endpoint is used directly for inline-review planning because
     * its destination path is authoritative and does not require parsing quoted
     * {@code diff --git} headers. A missing patch (for example, a binary or very
     * large file) is preserved as an empty value so callers can treat its lines
     * as unavailable for inline comments.</p>
     */
    public List<PullRequestFilePatch> getPullRequestFilePatches(
            String owner,
            String repo,
            int pullRequestNumber
    ) throws IOException {
        List<PullRequestFilePatch> patches = new ArrayList<>();
        String nextUrl = String.format("%s/repos/%s/%s/pulls/%d/files?per_page=100",
                GitHubConfig.API_BASE, owner, repo, pullRequestNumber);

        while (nextUrl != null) {
            Request req = new Request.Builder()
                    .url(nextUrl)
                    .header("Accept", "application/vnd.github.v3+json")
                    .header("X-GitHub-Api-Version", "2022-11-28")
                    .get()
                    .build();

            try (Response resp = authorizedOkHttpClient.newCall(req).execute()) {
                if (!resp.isSuccessful()) {
                    String body = resp.body() != null ? resp.body().string() : "";
                    String msg = String.format("GitHub returned non-success response %d for files endpoint: %s", resp.code(), body);
                    log.warn(msg);
                    throw new IOException(msg);
                }

                String responseBody = resp.body() != null ? resp.body().string() : "[]";
                JsonNode files = objectMapper.readTree(responseBody);

                for (JsonNode file : files) {
                    String filename = file.has("filename") ? file.get("filename").asText() : "";
                    String patch = file.has("patch") ? file.get("patch").asText() : "";
                    String status = file.has("status") ? file.get("status").asText() : "";
                    String previousFilename = file.has("previous_filename") ? file.get("previous_filename").asText() : "";
                    patches.add(new PullRequestFilePatch(
                            filename, previousFilename, status, patch));
                }

                // Check for next page in Link header
                nextUrl = null;
                String linkHeader = resp.header("Link");
                if (linkHeader != null) {
                    Matcher matcher = LINK_NEXT_PATTERN.matcher(linkHeader);
                    if (matcher.find()) {
                        nextUrl = matcher.group(1);
                    }
                }
            }
        }

        return List.copyOf(patches);
    }

    public record PullRequestFilePatch(
            String filename,
            String previousFilename,
            String status,
            String patch
    ) {
        public PullRequestFilePatch {
            filename = filename != null ? filename : "";
            previousFilename = previousFilename != null ? previousFilename : "";
            status = status != null ? status : "";
            patch = patch != null ? patch : "";
        }
    }
}
