package org.rostilos.codecrow.vcsclient.bitbucket.cloud.actions;

import okhttp3.OkHttpClient;
import okhttp3.Request;
import okhttp3.Response;
import org.rostilos.codecrow.vcsclient.bitbucket.cloud.BitbucketCloudConfig;

import java.io.IOException;

public record ValidateBitbucketCloudConnectionAction(OkHttpClient authorizedOkHttpClient) {

    public boolean isConnectionValid() {
        String url = BitbucketCloudConfig.BITBUCKET_API_BASE + "/user";

        Request request = new Request.Builder()
                .url(url)
                .header("Accept", "application/json")
                .get()
                .build();

        try (Response response = authorizedOkHttpClient.newCall(request).execute()) {
            assert response.body() != null;
            if (response.isSuccessful()) {
                return true;
            } else {
                System.err.println("Connection failed: " + response.code() + " - " + response.body().string());
                return false;
            }
        } catch (IOException e) {
            System.err.println("Connection error: " + e.getMessage());
            return false;
        }
    }
}