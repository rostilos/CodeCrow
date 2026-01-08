package org.rostilos.codecrow.mcp.gitlab;

import okhttp3.OkHttpClient;
import org.slf4j.Logger;
import org.slf4j.LoggerFactory;

import java.util.concurrent.TimeUnit;

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
        GitLabConfiguration configuration = new GitLabConfiguration(accessToken, namespace, project, mrIid);
        
        OkHttpClient httpClient = new OkHttpClient.Builder()
                .connectTimeout(30, TimeUnit.SECONDS)
                .readTimeout(60, TimeUnit.SECONDS)
                .writeTimeout(60, TimeUnit.SECONDS)
                .addInterceptor(chain -> {
                    okhttp3.Request originalRequest = chain.request();
                    okhttp3.Request.Builder builder = originalRequest.newBuilder()
                            .header("Authorization", "Bearer " + accessToken)
                            .header("Accept", "application/json");
                    return chain.proceed(builder.build());
                })
                .build();

        LOGGER.info("Created GitLab MCP client for {}/{}", namespace, project);
        return new GitLabMcpClientImpl(httpClient, configuration, fileLimit);
    }
}
