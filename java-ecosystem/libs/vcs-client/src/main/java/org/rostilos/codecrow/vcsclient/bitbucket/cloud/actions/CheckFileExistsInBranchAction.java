package org.rostilos.codecrow.vcsclient.bitbucket.cloud.actions;

import okhttp3.OkHttpClient;
import okhttp3.Request;
import okhttp3.Response;
import org.rostilos.codecrow.vcsclient.bitbucket.cloud.BitbucketCloudConfig;
import org.slf4j.Logger;
import org.slf4j.LoggerFactory;

import java.io.IOException;
import java.net.URLEncoder;
import java.nio.charset.StandardCharsets;
import java.util.Optional;

/**
 * Action to check if a file exists in a specific branch on Bitbucket Cloud.
 * Uses the Bitbucket Cloud API to verify file existence without downloading content.
 */
public class CheckFileExistsInBranchAction {

    private static final Logger log = LoggerFactory.getLogger(CheckFileExistsInBranchAction.class);
    private final OkHttpClient authorizedOkHttpClient;

    public CheckFileExistsInBranchAction(OkHttpClient authorizedOkHttpClient) {
        this.authorizedOkHttpClient = authorizedOkHttpClient;
    }

    /**
     * Checks if a file exists in the specified branch.
     * Uses HEAD request to check existence without downloading file content.
     *
     * @param workspace workspace or team slug
     * @param repoSlug repository slug
     * @param branchName branch name (or commit hash)
     * @param filePath file path relative to repository root
     * @return true if file exists in the branch, false otherwise
     * @throws IOException on network errors
     */
    public boolean fileExists(String workspace, String repoSlug, String branchName, String filePath) throws IOException {
        String ws = Optional.ofNullable(workspace).orElse("");
        String encodedPath = encodeFilePath(filePath);
        
        // Use Bitbucket Cloud API endpoint to check file existence with HEAD request
        String apiUrl = String.format("%s/repositories/%s/%s/src/%s/%s",
                BitbucketCloudConfig.BITBUCKET_API_BASE, ws, repoSlug, branchName, encodedPath);

        Request req = new Request.Builder()
                .url(apiUrl)
                .head()
                .build();

        try (Response resp = authorizedOkHttpClient.newCall(req).execute()) {
            if (resp.isSuccessful()) {
                return true;
            } else if (resp.code() == 404) {
                log.debug("File not found: {} in branch {} (workspace: {}, repo: {})", 
                    filePath, branchName, workspace, repoSlug);
                return false;
            } else {
                String msg = String.format("Unexpected response %d when checking file existence: %s in branch %s",
                        resp.code(), filePath, branchName);
                log.warn(msg);
                throw new IOException(msg);
            }
        } catch (IOException e) {
            log.error("Failed to check file existence for {}: {}", filePath, e.getMessage(), e);
            throw e;
        }
    }

    /**
     * Encodes file path for URL. Handles path segments properly.
     */
    private String encodeFilePath(String filePath) {
        if (filePath == null || filePath.isEmpty()) {
            return "";
        }
        
        // Split by / and encode each segment separately to preserve path structure
        String[] segments = filePath.split("/");
        StringBuilder encoded = new StringBuilder();
        
        for (int i = 0; i < segments.length; i++) {
            if (i > 0) {
                encoded.append("/");
            }
            encoded.append(URLEncoder.encode(segments[i], StandardCharsets.UTF_8));
        }
        
        return encoded.toString();
    }
}

