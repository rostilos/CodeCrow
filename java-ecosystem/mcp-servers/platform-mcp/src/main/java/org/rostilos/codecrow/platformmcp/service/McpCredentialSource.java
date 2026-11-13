package org.rostilos.codecrow.platformmcp.service;

import java.util.Map;
import java.util.Properties;

/** Reads request-scoped MCP credentials without placing them in process args. */
public final class McpCredentialSource {

    public static final String ACCESS_TOKEN_ENV = "CODECROW_MCP_ACCESS_TOKEN";
    public static final String OAUTH_CLIENT_ENV = "CODECROW_MCP_OAUTH_CLIENT";
    public static final String OAUTH_SECRET_ENV = "CODECROW_MCP_OAUTH_SECRET";
    public static final String INTERNAL_API_SECRET_ENV =
            "CODECROW_MCP_INTERNAL_API_SECRET";

    private McpCredentialSource() {
    }

    public static String accessToken() {
        return resolve(ACCESS_TOKEN_ENV, "accessToken");
    }

    public static String oauthClient() {
        return resolve(OAUTH_CLIENT_ENV, "oAuthClient");
    }

    public static String oauthSecret() {
        return resolve(OAUTH_SECRET_ENV, "oAuthSecret");
    }

    public static String internalApiSecret() {
        return resolve(INTERNAL_API_SECRET_ENV, "internal.api.secret");
    }

    static String resolve(
            String environmentName,
            String propertyName,
            Map<String, String> environment,
            Properties properties) {
        if (environment.containsKey(environmentName)) {
            return environment.get(environmentName);
        }
        return properties.getProperty(propertyName);
    }

    private static String resolve(String environmentName, String propertyName) {
        return resolve(
                environmentName,
                propertyName,
                System.getenv(),
                System.getProperties());
    }
}
