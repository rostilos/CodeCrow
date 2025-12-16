package org.rostilos.codecrow.vcsclient.github.actions;

import okhttp3.OkHttpClient;
import okhttp3.Request;
import okhttp3.Response;
import org.rostilos.codecrow.vcsclient.github.GitHubConfig;
import org.slf4j.Logger;
import org.slf4j.LoggerFactory;

import java.io.IOException;
import java.net.URLEncoder;
import java.nio.charset.StandardCharsets;

public class CheckFileExistsInBranchAction {

    private static final Logger log = LoggerFactory.getLogger(CheckFileExistsInBranchAction.class);
    private final OkHttpClient authorizedOkHttpClient;

    public CheckFileExistsInBranchAction(OkHttpClient authorizedOkHttpClient) {
        this.authorizedOkHttpClient = authorizedOkHttpClient;
    }

    public boolean fileExists(String owner, String repo, String branchName, String filePath) throws IOException {
        String encodedPath = encodeFilePath(filePath);
        String encodedRef = URLEncoder.encode(branchName, StandardCharsets.UTF_8);
        
        String apiUrl = String.format("%s/repos/%s/%s/contents/%s?ref=%s",
                GitHubConfig.API_BASE, owner, repo, encodedPath, encodedRef);

        Request req = new Request.Builder()
                .url(apiUrl)
                .header("Accept", "application/vnd.github+json")
                .header("X-GitHub-Api-Version", "2022-11-28")
                .head()
                .build();

        try (Response resp = authorizedOkHttpClient.newCall(req).execute()) {
            if (resp.isSuccessful()) {
                return true;
            } else if (resp.code() == 404) {
                log.debug("File not found: {} in branch {} (owner: {}, repo: {})", 
                    filePath, branchName, owner, repo);
                return false;
            } else {
                String msg = String.format("Unexpected response %d when checking file existence: %s in branch %s",
                        resp.code(), filePath, branchName);
                log.warn(msg);
                throw new IOException(msg);
            }
        }
    }

    private String encodeFilePath(String filePath) {
        if (filePath == null || filePath.isEmpty()) {
            return "";
        }
        String[] segments = filePath.split("/");
        StringBuilder encoded = new StringBuilder();
        for (int i = 0; i < segments.length; i++) {
            if (i > 0) encoded.append("/");
            encoded.append(URLEncoder.encode(segments[i], StandardCharsets.UTF_8));
        }
        return encoded.toString();
    }
}
