package org.rostilos.codecrow.vcsclient.gitlab.actions;

import okhttp3.OkHttpClient;
import okhttp3.Request;
import okhttp3.Response;
import org.rostilos.codecrow.vcsclient.gitlab.GitLabConfig;
import org.slf4j.Logger;
import org.slf4j.LoggerFactory;

import java.io.IOException;
import java.net.URLEncoder;
import java.nio.charset.StandardCharsets;

/**
 * Action to check if a file exists in a specific branch in GitLab.
 */
public class CheckFileExistsInBranchAction {

    private static final Logger log = LoggerFactory.getLogger(CheckFileExistsInBranchAction.class);
    private final OkHttpClient authorizedOkHttpClient;

    public CheckFileExistsInBranchAction(OkHttpClient authorizedOkHttpClient) {
        this.authorizedOkHttpClient = authorizedOkHttpClient;
    }

    /**
     * Check if a file exists in a specific branch.
     * 
     * @param namespace the project namespace (group or user)
     * @param project the project path
     * @param branchName the branch name
     * @param filePath the file path to check
     * @return true if the file exists, false otherwise
     */
    public boolean fileExists(String namespace, String project, String branchName, String filePath) throws IOException {
        String projectPath = namespace + "/" + project;
        String encodedPath = URLEncoder.encode(projectPath, StandardCharsets.UTF_8);
        String encodedFilePath = URLEncoder.encode(filePath, StandardCharsets.UTF_8);
        
        String apiUrl = String.format("%s/projects/%s/repository/files/%s?ref=%s",
                GitLabConfig.API_BASE, encodedPath, encodedFilePath,
                URLEncoder.encode(branchName, StandardCharsets.UTF_8));

        Request req = new Request.Builder()
                .url(apiUrl)
                .header("Accept", "application/json")
                .head()
                .build();

        try (Response resp = authorizedOkHttpClient.newCall(req).execute()) {
            if (resp.code() == 404) {
                return false;
            }
            if (!resp.isSuccessful()) {
                String body = resp.body() != null ? resp.body().string() : "";
                log.warn("GitLab returned non-success response {} for file check: {}", resp.code(), body);
                throw new IOException("Failed to check file existence: " + resp.code());
            }
            return true;
        }
    }
}
