package org.rostilos.codecrow.vcsclient.bitbucket.cloud.actions;

import okhttp3.OkHttpClient;
import okhttp3.Request;
import okhttp3.Response;
import org.rostilos.codecrow.vcsclient.bitbucket.cloud.BitbucketCloudConfig;
import org.slf4j.Logger;
import org.slf4j.LoggerFactory;

import java.io.IOException;
import java.util.Optional;

/**
 * Action to retrieve diff between two commits (commit range) from Bitbucket Cloud.
 * Used for incremental/delta analysis to get only changes since the last analyzed commit.
 */
public class GetCommitRangeDiffAction {

    private static final Logger log = LoggerFactory.getLogger(GetCommitRangeDiffAction.class);
    private final OkHttpClient authorizedOkHttpClient;

    public GetCommitRangeDiffAction(OkHttpClient authorizedOkHttpClient) {
        this.authorizedOkHttpClient = authorizedOkHttpClient;
    }

    /**
     * Fetches the diff between two commits.
     * 
     * Bitbucket API: GET /repositories/{workspace}/{repo_slug}/diff/{spec}?topic=true
     * where spec uses Bitbucket's source..destination order. Bitbucket's order is
     * the opposite of {@code git diff}; {@code topic=true} selects its
     * merge-base-aware PR/topic diff.
     *
     * @param workspace workspace or team slug
     * @param repoSlug repository slug
     * @param baseCommitHash the base commit (previously analyzed commit)
     * @param headCommitHash the head commit (current commit to analyze)
     * @return raw unified diff between the two commits
     * @throws IOException on network / parsing errors
     */
    public String getCommitRangeDiff(String workspace, String repoSlug, String baseCommitHash, String headCommitHash) throws IOException {
        String ws = Optional.ofNullable(workspace).orElse("");
        String displayWorkspace = ws.isEmpty() ? "(no-workspace)" : ws;
        
        // Bitbucket accepts two commits separated by "..", not Git's "..."
        // syntax. It also defines the first commit as the source/topic and the
        // second as the destination, which is the reverse of git diff order.
        // topic=true makes the response merge-base-aware so destination-only
        // changes are not reported as removals from the PR.
        String spec = headCommitHash + ".." + baseCommitHash;
        String apiUrl = String.format("%s/repositories/%s/%s/diff/%s?topic=true",
                BitbucketCloudConfig.BITBUCKET_API_BASE, ws, repoSlug, spec);

        log.info("Fetching commit range diff: {}/{} from {} to {}",
                displayWorkspace, repoSlug,
                baseCommitHash.length() >= 7 ? baseCommitHash.substring(0, 7) : baseCommitHash,
                headCommitHash.length() >= 7 ? headCommitHash.substring(0, 7) : headCommitHash);

        Request req = new Request.Builder()
                .url(apiUrl)
                .get()
                .build();

        try (Response resp = authorizedOkHttpClient.newCall(req).execute()) {
            if (!resp.isSuccessful()) {
                String body = resp.body() != null ? resp.body().string() : "";
                String msg = String.format("Bitbucket returned non-success response %d for commit range diff URL %s: %s",
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
