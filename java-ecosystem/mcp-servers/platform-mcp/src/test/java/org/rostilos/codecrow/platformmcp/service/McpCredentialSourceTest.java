package org.rostilos.codecrow.platformmcp.service;

import static org.assertj.core.api.Assertions.assertThat;

import java.util.Map;
import java.util.Properties;

import org.junit.jupiter.api.Test;

class McpCredentialSourceTest {

    @Test
    void readsInternalSecretFromScopedEnvironmentBeforeLegacyProperty() {
        Properties properties = new Properties();
        properties.setProperty("internal.api.secret", "legacy-property-secret");

        assertThat(McpCredentialSource.resolve(
                McpCredentialSource.INTERNAL_API_SECRET_ENV,
                "internal.api.secret",
                Map.of(
                        McpCredentialSource.INTERNAL_API_SECRET_ENV,
                        "scoped-environment-secret"),
                properties))
                .isEqualTo("scoped-environment-secret");
    }

    @Test
    void retainsSystemPropertyCompatibilityWhenEnvironmentIsAbsent() {
        Properties properties = new Properties();
        properties.setProperty("oAuthClient", "legacy-property-client");

        assertThat(McpCredentialSource.resolve(
                McpCredentialSource.OAUTH_CLIENT_ENV,
                "oAuthClient",
                Map.of(),
                properties))
                .isEqualTo("legacy-property-client");
    }
}
