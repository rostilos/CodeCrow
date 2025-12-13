package org.rostilos.codecrow.vcsclient.github;

public final class GitHubConfig {
    
    public static final String API_BASE = "https://api.github.com";
    public static final String OAUTH_AUTHORIZE_URL = "https://github.com/login/oauth/authorize";
    public static final String OAUTH_TOKEN_URL = "https://github.com/login/oauth/access_token";
    
    public static final String APP_INSTALLATIONS_URL = API_BASE + "/app/installations";
    
    public static final int DEFAULT_PAGE_SIZE = 30;
    
    private GitHubConfig() {
        // Utility class
    }
}
