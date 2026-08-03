package org.rostilos.codecrow.mcp.gitlab;

import org.rostilos.codecrow.vcsclient.gitlab.GitLabClient;
import org.slf4j.Logger;
import org.slf4j.LoggerFactory;

/**
 * Factory for creating GitLab MCP clients.
 */
public class GitLabClientFactory {

    private static final Logger LOGGER = LoggerFactory.getLogger(GitLabClientFactory.class);

    public GitLabMcpClientImpl createClient() {
        // Use the same property names as other providers for consistency
        String accessToken = System.getProperty("accessToken");
        String namespace = System.getProperty("workspace");  // GitLab uses namespace, but we receive workspace
        String project = System.getProperty("repo.slug");
        String mrIid = System.getProperty("pullRequest.id");  // MR IID in GitLab
        String baseUrl = System.getProperty("vcs.baseUrl");

        if (accessToken == null || accessToken.isEmpty()) {
            throw new IllegalStateException("accessToken system property is required for GitLab");
        }
        if (namespace == null || namespace.isEmpty()) {
            throw new IllegalStateException("workspace system property is required for GitLab");
        }
        if (project == null || project.isEmpty()) {
            throw new IllegalStateException("repo.slug system property is required for GitLab");
        }

        int fileLimit = Integer.parseInt(System.getProperty("file.limit", "0"));
        GitLabConfiguration configuration = new GitLabConfiguration(
                accessToken, namespace, project, mrIid, baseUrl);

        LOGGER.info("Created GitLab MCP client for {}/{} on {}",
                namespace, project, configuration.getBaseUrl());
        GitLabClient gitLabClient =
                org.rostilos.codecrow.vcsclient.gitlab.GitLabClientFactory
                        .createWithAccessToken(
                                accessToken, configuration.getBaseUrl());
        return new GitLabMcpClientImpl(gitLabClient, configuration, fileLimit);
    }
}
