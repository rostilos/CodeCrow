package org.rostilos.codecrow.vcsclient.gitlab.actions;

import okhttp3.OkHttpClient;
import okhttp3.Request;
import okhttp3.Response;
import org.rostilos.codecrow.vcsclient.gitlab.GitLabConfig;
import org.slf4j.Logger;
import org.slf4j.LoggerFactory;

import java.io.IOException;

/**
 * Action to validate a GitLab connection.
 */
public class ValidateConnectionAction {

    private static final Logger log = LoggerFactory.getLogger(ValidateConnectionAction.class);
    private final OkHttpClient authorizedOkHttpClient;

    public ValidateConnectionAction(OkHttpClient authorizedOkHttpClient) {
        this.authorizedOkHttpClient = authorizedOkHttpClient;
    }

    /**
     * Check if the connection is valid by calling the /user endpoint.
     */
    public boolean isConnectionValid() {
        String apiUrl = GitLabConfig.API_BASE + "/user";

        Request req = new Request.Builder()
                .url(apiUrl)
                .header("Accept", "application/json")
                .get()
                .build();

        try (Response resp = authorizedOkHttpClient.newCall(req).execute()) {
            return resp.isSuccessful();
        } catch (IOException e) {
            log.warn("Failed to validate GitLab connection: {}", e.getMessage());
            return false;
        }
    }
}
