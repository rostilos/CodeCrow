package org.rostilos.codecrow.vcsclient;

import okhttp3.OkHttpClient;
import org.rostilos.codecrow.core.model.vcs.EVcsProvider;

public interface HttpAuthorizedClient {
    EVcsProvider getGitPlatform();
    OkHttpClient createClient(String clientId, String clientSecret);
}
