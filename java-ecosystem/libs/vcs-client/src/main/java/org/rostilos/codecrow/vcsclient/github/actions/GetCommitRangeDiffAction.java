package org.rostilos.codecrow.vcsclient.github.actions;

import okhttp3.OkHttpClient;
import okhttp3.Request;
import okhttp3.Response;
import org.rostilos.codecrow.vcsclient.github.GitHubConfig;
import org.slf4j.Logger;
import org.slf4j.LoggerFactory;

import java.io.IOException;

/**
 * Action to retrieve diff between two commits (commit range) from GitHub.
 * Used for incremental/delta analysis to get only changes since the last analyzed commit.
 */
public class GetCommitRangeDiffAction {

    private static final Logger log = LoggerFactory.getLogger(GetCommitRangeDiffAction.class);
    private final OkHttpClient authorizedOkHttpClient;

    public GetCommitRangeDiffAction(OkHttpClient authorizedOkHttpClient) {
        this.authorizedOkHttpClient = authorizedOkHttpClient;
    }

    /**
     * Fetches the diff between two commits using GitHub compare API.
     * 
     * GitHub API: GET /repos/{owner}/{repo}/compare/{basehead}
     * where basehead is "base...head" format
     *
     * @param owner repository owner
     * @param repo repository name
     * @param baseCommitHash the base commit (previously analyzed commit)
     * @param headCommitHash the head commit (current commit to analyze)
     * @return raw unified diff between the two commits
     * @throws IOException on network / parsing errors
     */
    public String getCommitRangeDiff(String owner, String repo, String baseCommitHash, String headCommitHash) throws IOException {
        // GitHub uses the basehead format: base...head (three dots for merge-base comparison)
        // Using two dots (..) would give direct comparison, but three dots is more common for PRs
        String basehead = baseCommitHash + "..." + headCommitHash;
        String apiUrl = String.format("%s/repos/%s/%s/compare/%s",
                GitHubConfig.API_BASE, owner, repo, basehead);

        log.info("Fetching commit range diff: {}/{} from {} to {}", 
                owner, repo, 
                baseCommitHash.length() >= 7 ? baseCommitHash.substring(0, 7) : baseCommitHash, 
                headCommitHash.length() >= 7 ? headCommitHash.substring(0, 7) : headCommitHash);

        Request req = new Request.Builder()
                .url(apiUrl)
                .header("Accept", "application/vnd.github.v3.diff")
                .header("X-GitHub-Api-Version", "2022-11-28")
                .get()
                .build();

        try (Response resp = authorizedOkHttpClient.newCall(req).execute()) {
            if (!resp.isSuccessful()) {
                String body = resp.body() != null ? resp.body().string() : "";
                String msg = String.format("GitHub returned non-success response %d for commit range diff URL %s: %s",
                        resp.code(), apiUrl, body);
                log.warn(msg);
                throw new IOException(msg);
            }
            String diff = resp.body() != null ? resp.body().string() : "";
            log.info("Retrieved commit range diff: {} chars", diff.length());
            return diff;
        } catch (IOException e) {
            log.error("Failed to get commit range diff: {}", e.getMessage(), e);
            throw e;
        }
    }
}
