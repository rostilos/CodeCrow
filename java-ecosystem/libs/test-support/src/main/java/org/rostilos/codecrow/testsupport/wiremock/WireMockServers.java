package org.rostilos.codecrow.testsupport.wiremock;

import com.github.tomakehurst.wiremock.WireMockServer;
import com.github.tomakehurst.wiremock.core.WireMockConfiguration;

/**
 * Factory for creating preconfigured WireMock servers for different VCS providers and services.
 */
public final class WireMockServers {

    private WireMockServers() {
    }

    public static WireMockServer createGitHub() {
        return create("GitHub");
    }

    public static WireMockServer createGitLab() {
        return create("GitLab");
    }

    public static WireMockServer createBitbucketCloud() {
        return create("BitbucketCloud");
    }

    public static WireMockServer createOpenAi() {
        return create("OpenAI");
    }

    public static WireMockServer createAnthropic() {
        return create("Anthropic");
    }

    public static WireMockServer createOpenRouter() {
        return create("OpenRouter");
    }

    public static WireMockServer createRagPipeline() {
        return create("RagPipeline");
    }

    private static WireMockServer create(String name) {
        WireMockServer server = new WireMockServer(
                WireMockConfiguration.wireMockConfig()
                        .dynamicPort()
                        .usingFilesUnderClasspath("wiremock/" + name.toLowerCase())
        );
        server.start();
        return server;
    }

    /**
     * Apply the base URL of a WireMock server as a system property.
     */
    public static void applyProperty(String propertyKey, WireMockServer server) {
        System.setProperty(propertyKey, server.baseUrl());
    }
}
