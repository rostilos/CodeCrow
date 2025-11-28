package org.rostilos.codecrow.vcsclient;

import okhttp3.*;
import org.rostilos.codecrow.core.model.vcs.EVcsProvider;
import org.springframework.stereotype.Component;

import java.util.List;
import java.util.Map;
import java.util.stream.Collectors;

@Component
public class HttpAuthorizedClientFactory {
    private final Map<EVcsProvider, HttpAuthorizedClient> delegateMap;

    public HttpAuthorizedClientFactory(
            List<HttpAuthorizedClient> delegates
    ) {
        this.delegateMap = delegates.stream().collect(Collectors.toMap(HttpAuthorizedClient::getGitPlatform, d -> d));
    }

    public OkHttpClient createClient(
            String clientId,
            String clientSecret,
            String gitPlatformId
    ) {
        validateSettings(clientId, clientSecret);
        EVcsProvider gitProvider = EVcsProvider.fromId(gitPlatformId);
        HttpAuthorizedClient delegate = delegateMap.get(gitProvider);
        if (delegate == null) {
            throw new IllegalArgumentException("No factory for Git Provider: " + gitPlatformId);
        }

        return delegate.createClient(clientId, clientSecret);
    }

    private void validateSettings(String clientId, String clientSecret) {
        if (clientId.isEmpty()) {
            throw new IllegalArgumentException("No ClientId key has been set for Bitbucket connections");
        }
        if (clientSecret.isEmpty()) {
            throw new IllegalArgumentException("No ClientSecret has been set for Bitbucket connections");
        }
    }
}
