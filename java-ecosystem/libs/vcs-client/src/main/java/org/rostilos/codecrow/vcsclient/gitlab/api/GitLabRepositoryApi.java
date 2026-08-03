package org.rostilos.codecrow.vcsclient.gitlab.api;

import okhttp3.Response;

import java.io.IOException;

/**
 * Focused GitLab repository operations used by the shared client.
 */
public final class GitLabRepositoryApi {

    private final GitLabApiContext api;

    public GitLabRepositoryApi(GitLabApiContext api) {
        this.api = api;
    }

    public boolean fileExists(
            String namespace,
            String project,
            String branchOrCommit,
            String filePath
    ) throws IOException {
        String url = api.projectUrl(namespace, project)
                + "/repository/files/" + api.encode(filePath)
                + "?ref=" + api.encode(branchOrCommit);
        try (Response response = api.execute(api.head(url))) {
            if (response.code() == 404) {
                return false;
            }
            if (!response.isSuccessful()) {
                throw api.error("check file existence", response);
            }
            return true;
        }
    }

    public String getTree(
            String namespace,
            String project,
            String branchOrCommit,
            String directoryPath
    ) throws IOException {
        StringBuilder url = new StringBuilder(api.projectUrl(namespace, project))
                .append("/repository/tree?ref=")
                .append(api.encode(branchOrCommit));
        if (directoryPath != null && !directoryPath.isBlank()) {
            url.append("&path=").append(api.encode(directoryPath));
        }
        try (Response response = api.execute(api.get(url.toString()))) {
            if (!response.isSuccessful()) {
                throw api.error("get repository tree", response);
            }
            return api.bodyOr(response, "[]");
        }
    }
}
