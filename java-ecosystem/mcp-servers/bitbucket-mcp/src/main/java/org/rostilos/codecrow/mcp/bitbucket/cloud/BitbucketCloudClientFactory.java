package org.rostilos.codecrow.mcp.bitbucket.cloud;

import org.rostilos.codecrow.mcp.bitbucket.BitbucketConfiguration;
import org.slf4j.Logger;
import org.slf4j.LoggerFactory;

import okhttp3.OkHttpClient;
import okhttp3.Request;

public class BitbucketCloudClientFactory {

    private static final Logger LOGGER = LoggerFactory.getLogger(BitbucketCloudClientFactory.class);

    public BitbucketCloudClient createClient() {
        validateRequest();

        String pullRequestId = System.getProperty("pullRequest.id");
        String workspace = System.getProperty("workspace");
        String repoSlug = System.getProperty("repo.slug");
        String oAuthClient = System.getProperty("oAuthClient");
        String oAuthSecret = System.getProperty("oAuthSecret");


        String bearerToken = BitbucketCloudClientImpl.negotiateBearerToken(
                oAuthClient,
                oAuthSecret,
                new com.fasterxml.jackson.databind.ObjectMapper(),
                new OkHttpClient()
        );

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
        //TODO: Throw error
        if(System.getProperty("project.id") == null) {

        }
        if(System.getProperty("pullRequest.id") == null) {

        }

        if(System.getProperty("workspace") == null) {

        }
        if(System.getProperty("repo.slug") == null) {

        }
        if(System.getProperty("oAuthClient") == null) {

        }
        if(System.getProperty("oAuthSecret") == null) {

        }

    }
}
