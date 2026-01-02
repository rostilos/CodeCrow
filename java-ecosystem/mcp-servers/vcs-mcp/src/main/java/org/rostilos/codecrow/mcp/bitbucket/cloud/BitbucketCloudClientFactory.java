package org.rostilos.codecrow.mcp.bitbucket.cloud;

import org.rostilos.codecrow.mcp.bitbucket.BitbucketConfiguration;
import org.rostilos.codecrow.mcp.generic.VcsMcpClient;
import org.slf4j.Logger;
import org.slf4j.LoggerFactory;

import okhttp3.OkHttpClient;
import okhttp3.Request;

public class BitbucketCloudClientFactory {

    private static final Logger LOGGER = LoggerFactory.getLogger(BitbucketCloudClientFactory.class);

    public VcsMcpClient createClient() {
        return new BitbucketCloudClientAdapter(createBitbucketClient());
    }

    public BitbucketCloudClient createBitbucketClient() {
        validateRequest();

        String pullRequestId = System.getProperty("pullRequest.id");
        String workspace = System.getProperty("workspace");
        String repoSlug = System.getProperty("repo.slug");
        
        String accessToken = System.getProperty("accessToken");
        String bearerToken;
        
        if (accessToken != null && !accessToken.isEmpty()) {
            LOGGER.info("Using provided access token for authentication");
            bearerToken = accessToken;
        } else {
            String oAuthClient = System.getProperty("oAuthClient");
            String oAuthSecret = System.getProperty("oAuthSecret");
            LOGGER.info("Using OAuth client credentials for authentication");
            bearerToken = BitbucketCloudClientImpl.negotiateBearerToken(
                    oAuthClient,
                    oAuthSecret,
                    new com.fasterxml.jackson.databind.ObjectMapper(),
                    new OkHttpClient()
            );
        }

        OkHttpClient okHttpClient = new OkHttpClient.Builder()
                .addInterceptor(chain -> {
                    Request newRequest = chain.request().newBuilder()
                            .addHeader("Authorization", "Bearer " + bearerToken)
                            .addHeader("Accept", "application/json")
                            .build();
                    return chain.proceed(newRequest);
                })
                .build();


        BitbucketConfiguration bitbucketConfiguration = new BitbucketConfiguration(workspace, repoSlug);

        return new BitbucketCloudClientImpl(
                okHttpClient,
                bitbucketConfiguration,
                0,
                workspace,
                repoSlug,
                pullRequestId
        );
    }

    private void validateRequest() {
        //TODO: validate
//        if(System.getProperty("project.id") == null) {
//
//        }
//        if(System.getProperty("pullRequest.id") == null) {
//
//        }
//
//        if(System.getProperty("workspace") == null) {
//
//        }
//        if(System.getProperty("repo.slug") == null) {
//
//        }
        
        String accessToken = System.getProperty("accessToken");
        if (accessToken == null || accessToken.isEmpty()) {
            if(System.getProperty("oAuthClient") == null) {
                LOGGER.warn("Neither accessToken nor oAuthClient provided");
            }
            if(System.getProperty("oAuthSecret") == null) {
                LOGGER.warn("Neither accessToken nor oAuthSecret provided");
            }
        }

    }
}
