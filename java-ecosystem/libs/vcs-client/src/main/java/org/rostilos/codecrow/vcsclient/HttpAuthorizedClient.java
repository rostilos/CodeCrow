package org.rostilos.codecrow.vcsclient;

import okhttp3.OkHttpClient;
import org.rostilos.codecrow.core.model.vcs.EVcsProvider;

public interface HttpAuthorizedClient {
    EVcsProvider getGitPlatform();

    AuthorizedVcsTransport createTransport(String clientId, String clientSecret);

    default OkHttpClient createClient(String clientId, String clientSecret) {
        return createTransport(clientId, clientSecret).httpClient();
    }
}
