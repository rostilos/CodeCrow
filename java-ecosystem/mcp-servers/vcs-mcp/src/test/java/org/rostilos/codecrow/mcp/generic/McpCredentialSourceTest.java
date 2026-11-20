package org.rostilos.codecrow.mcp.generic;

import static org.assertj.core.api.Assertions.assertThat;

import java.util.Map;
import java.util.Properties;

import org.junit.jupiter.api.Test;

class McpCredentialSourceTest {

    @Test
    void readsCredentialFromScopedEnvironmentBeforeLegacyProperty() {
        Properties properties = new Properties();
        properties.setProperty("accessToken", "legacy-property-token");

        assertThat(McpCredentialSource.resolve(
                McpCredentialSource.ACCESS_TOKEN_ENV,
                "accessToken",
                Map.of(McpCredentialSource.ACCESS_TOKEN_ENV, "scoped-environment-token"),
                properties))
                .isEqualTo("scoped-environment-token");
    }

    @Test
    void retainsSystemPropertyCompatibilityWhenEnvironmentIsAbsent() {
        Properties properties = new Properties();
        properties.setProperty("oAuthSecret", "legacy-property-secret");

        assertThat(McpCredentialSource.resolve(
                McpCredentialSource.OAUTH_SECRET_ENV,
                "oAuthSecret",
                Map.of(),
                properties))
                .isEqualTo("legacy-property-secret");
    }
}
