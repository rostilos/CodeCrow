package org.rostilos.codecrow.vcsclient.github.actions;

import okhttp3.OkHttpClient;
import okhttp3.Request;
import okhttp3.Response;
import org.rostilos.codecrow.vcsclient.github.GitHubConfig;
import org.slf4j.Logger;
import org.slf4j.LoggerFactory;

import java.io.IOException;

public class ValidateConnectionAction {

    private static final Logger log = LoggerFactory.getLogger(ValidateConnectionAction.class);
    private final OkHttpClient authorizedOkHttpClient;

    public ValidateConnectionAction(OkHttpClient authorizedOkHttpClient) {
        this.authorizedOkHttpClient = authorizedOkHttpClient;
    }

    public boolean isConnectionValid() {
        String apiUrl = GitHubConfig.API_BASE + "/user";

        Request req = new Request.Builder()
                .url(apiUrl)
                .header("Accept", "application/vnd.github+json")
                .header("X-GitHub-Api-Version", "2022-11-28")
                .get()
                .build();

        try (Response resp = authorizedOkHttpClient.newCall(req).execute()) {
            return resp.isSuccessful();
        } catch (IOException e) {
            log.warn("Failed to validate GitHub connection: {}", e.getMessage());
            return false;
        }
    }
}
