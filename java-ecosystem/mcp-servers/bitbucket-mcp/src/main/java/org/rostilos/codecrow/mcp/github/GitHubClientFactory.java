package org.rostilos.codecrow.mcp.github;

import okhttp3.OkHttpClient;
import org.slf4j.Logger;
import org.slf4j.LoggerFactory;

import java.io.IOException;
import java.util.concurrent.TimeUnit;

public class GitHubClientFactory {

    private static final Logger LOGGER = LoggerFactory.getLogger(GitHubClientFactory.class);

    public GitHubMcpClientImpl createClient() {
        // Use the same property names as BitbucketCloudClientFactory for consistency
        String accessToken = System.getProperty("accessToken");
        String owner = System.getProperty("workspace");  // GitHub uses owner, but we receive workspace
        String repo = System.getProperty("repo.slug");
        String prNumber = System.getProperty("pullRequest.id");

        if (accessToken == null || accessToken.isEmpty()) {
            throw new IllegalStateException("accessToken system property is required for GitHub");
        }
        if (owner == null || owner.isEmpty()) {
            throw new IllegalStateException("workspace system property is required for GitHub");
        }
        if (repo == null || repo.isEmpty()) {
            throw new IllegalStateException("repo.slug system property is required for GitHub");
        }

        int fileLimit = Integer.parseInt(System.getProperty("file.limit", "0"));
        GitHubConfiguration configuration = new GitHubConfiguration(accessToken, owner, repo, prNumber);
        
        OkHttpClient httpClient = new OkHttpClient.Builder()
                .connectTimeout(30, TimeUnit.SECONDS)
                .readTimeout(60, TimeUnit.SECONDS)
                .writeTimeout(60, TimeUnit.SECONDS)
                .addInterceptor(chain -> {
                    okhttp3.Request.Builder builder = chain.request().newBuilder()
                            .header("Authorization", "Bearer " + accessToken)
                            .header("X-GitHub-Api-Version", "2022-11-28");
                    if (chain.request().header("Accept") == null) {
                        builder.header("Accept", "application/vnd.github+json");
                    }
                    return chain.proceed(builder.build());
                })
                .build();

        LOGGER.info("Created GitHub MCP client for {}/{}", owner, repo);
        return new GitHubMcpClientImpl(httpClient, configuration, fileLimit);
    }
}
