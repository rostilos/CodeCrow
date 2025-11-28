package org.rostilos.codecrow.vcsclient.bitbucket.cloud;

import com.fasterxml.jackson.databind.JsonNode;
import com.fasterxml.jackson.databind.ObjectMapper;
import okhttp3.*;
import org.rostilos.codecrow.core.model.vcs.EVcsProvider;
import org.rostilos.codecrow.vcsclient.HttpAuthorizedClient;
import org.springframework.stereotype.Component;

import java.io.IOException;
import java.nio.charset.StandardCharsets;
import java.util.Base64;

import static java.lang.String.format;

@Component
public class BitbucketCloudHttpAuthorizedClient implements HttpAuthorizedClient {
    private final OkHttpClient.Builder clientBuilder;
    private final ObjectMapper objectMapper;

    public BitbucketCloudHttpAuthorizedClient(
            OkHttpClient.Builder clientBuilder,
            ObjectMapper objectMapper
    ) {
        this.clientBuilder = clientBuilder;
        this.objectMapper = objectMapper;
    }

    @Override
    public EVcsProvider getGitPlatform() {
        return EVcsProvider.BITBUCKET_CLOUD;
    }


    @Override
    public OkHttpClient createClient(String clientId, String clientSecret) {
        String bearerToken = negotiateBearerToken(clientId, clientSecret, clientBuilder.build());
        return createAuthorisingClient(bearerToken);

    }

    public OkHttpClient createAuthorisingClient(String bearerToken) {
        return new OkHttpClient.Builder()
                .addInterceptor(chain -> {
                    Request newRequest = chain.request().newBuilder()
                            .addHeader("Authorization", format("Bearer %s", bearerToken))
                            .addHeader("Accept", "application/json")
                            .build();
                    return chain.proceed(newRequest);
                })
                .build();
    }

    private String negotiateBearerToken(String clientId, String clientSecret, OkHttpClient okHttpClient) {
        Request request = new Request.Builder()
                .header("Authorization", "Basic " + Base64.getEncoder().encodeToString((clientId + ":" + clientSecret).getBytes(StandardCharsets.UTF_8)))
                .header("Accept", "application/json")
                .url("https://bitbucket.org/site/oauth2/access_token")
                .post(new FormBody.Builder()
                        .add("grant_type", "client_credentials")
                        .build())
                .build();

        try (Response response = okHttpClient.newCall(request).execute()) {
            if (!response.isSuccessful()) {
                throw new IllegalStateException("Failed to authenticate: " + response.code() + " - " + response.body().string());
            }

            assert response.body() != null;
            JsonNode node = objectMapper.readTree(response.body().string());
            return node.get("access_token").asText();
        } catch (IOException ex) {
            throw new IllegalStateException("Could not retrieve bearer token", ex);
        }
    }
}
