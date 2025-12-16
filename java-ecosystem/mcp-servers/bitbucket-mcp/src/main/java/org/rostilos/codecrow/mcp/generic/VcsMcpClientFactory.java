package org.rostilos.codecrow.mcp.generic;

import org.rostilos.codecrow.mcp.bitbucket.cloud.BitbucketCloudClientFactory;
import org.rostilos.codecrow.mcp.github.GitHubClientFactory;
import org.slf4j.Logger;
import org.slf4j.LoggerFactory;

import java.io.IOException;

public class VcsMcpClientFactory {

    private static final Logger LOGGER = LoggerFactory.getLogger(VcsMcpClientFactory.class);
    
    public static final String PROVIDER_BITBUCKET = "bitbucket";
    public static final String PROVIDER_GITHUB = "github";

    public VcsMcpClient createClient() throws IOException {
        String vcsProvider = System.getProperty("vcs.provider", PROVIDER_BITBUCKET);
        LOGGER.info("Creating VCS MCP client for provider: {}", vcsProvider);
        
        return switch (vcsProvider.toLowerCase()) {
            case PROVIDER_BITBUCKET, "bitbucket_cloud", "bitbucket-cloud" -> 
                    new BitbucketCloudClientFactory().createClient();
            case PROVIDER_GITHUB -> 
                    new GitHubClientFactory().createClient();
            default -> throw new IllegalArgumentException("Unsupported VCS provider: " + vcsProvider);
        };
    }
    
    public VcsMcpClient createClient(String provider) throws IOException {
        return switch (provider.toLowerCase()) {
            case PROVIDER_BITBUCKET, "bitbucket_cloud", "bitbucket-cloud" -> 
                    new BitbucketCloudClientFactory().createClient();
            case PROVIDER_GITHUB -> 
                    new GitHubClientFactory().createClient();
            default -> throw new IllegalArgumentException("Unsupported VCS provider: " + provider);
        };
    }
}
