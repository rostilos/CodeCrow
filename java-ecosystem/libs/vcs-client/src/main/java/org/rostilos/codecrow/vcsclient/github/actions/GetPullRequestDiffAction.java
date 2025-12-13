package org.rostilos.codecrow.vcsclient.github.actions;

import okhttp3.OkHttpClient;
import okhttp3.Request;
import okhttp3.Response;
import org.rostilos.codecrow.vcsclient.github.GitHubConfig;
import org.slf4j.Logger;
import org.slf4j.LoggerFactory;

import java.io.IOException;

public class GetPullRequestDiffAction {

    private static final Logger log = LoggerFactory.getLogger(GetPullRequestDiffAction.class);
    private final OkHttpClient authorizedOkHttpClient;

    public GetPullRequestDiffAction(OkHttpClient authorizedOkHttpClient) {
        this.authorizedOkHttpClient = authorizedOkHttpClient;
    }

    public String getPullRequestDiff(String owner, String repo, int pullRequestNumber) throws IOException {
        String apiUrl = String.format("%s/repos/%s/%s/pulls/%d",
                GitHubConfig.API_BASE, owner, repo, pullRequestNumber);

        Request req = new Request.Builder()
                .url(apiUrl)
                .header("Accept", "application/vnd.github.diff")
                .header("X-GitHub-Api-Version", "2022-11-28")
                .get()
                .build();

        try (Response resp = authorizedOkHttpClient.newCall(req).execute()) {
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
}
