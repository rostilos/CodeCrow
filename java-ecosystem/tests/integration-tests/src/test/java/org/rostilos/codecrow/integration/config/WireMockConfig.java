package org.rostilos.codecrow.integration.config;

import com.github.tomakehurst.wiremock.WireMockServer;
import com.github.tomakehurst.wiremock.core.WireMockConfiguration;
import jakarta.annotation.PreDestroy;
import org.springframework.boot.test.context.TestConfiguration;
import org.springframework.context.annotation.Bean;

/**
 * WireMock configuration for mocking external services in integration tests.
 * Provides mock servers for VCS providers and AI services.
 */
@TestConfiguration
public class WireMockConfig {

    private WireMockServer bitbucketCloudMock;
    private WireMockServer bitbucketServerMock;
    private WireMockServer gitlabMock;
    private WireMockServer githubMock;
    private WireMockServer openaiMock;
    private WireMockServer anthropicMock;
    private WireMockServer openrouterMock;
    private WireMockServer ragPipelineMock;

    @Bean(name = "bitbucketCloudMock")
    public WireMockServer bitbucketCloudMock() {
        bitbucketCloudMock = new WireMockServer(WireMockConfiguration.options()
                .dynamicPort()
                .usingFilesUnderClasspath("wiremock/bitbucket-cloud"));
        bitbucketCloudMock.start();
        return bitbucketCloudMock;
    }

    @Bean(name = "bitbucketServerMock")
    public WireMockServer bitbucketServerMock() {
        bitbucketServerMock = new WireMockServer(WireMockConfiguration.options()
                .dynamicPort()
                .usingFilesUnderClasspath("wiremock/bitbucket-server"));
        bitbucketServerMock.start();
        return bitbucketServerMock;
    }

    @Bean(name = "gitlabMock")
    public WireMockServer gitlabMock() {
        gitlabMock = new WireMockServer(WireMockConfiguration.options()
                .dynamicPort()
                .usingFilesUnderClasspath("wiremock/gitlab"));
        gitlabMock.start();
        return gitlabMock;
    }

    @Bean(name = "githubMock")
    public WireMockServer githubMock() {
        githubMock = new WireMockServer(WireMockConfiguration.options()
                .dynamicPort()
                .usingFilesUnderClasspath("wiremock/github"));
        githubMock.start();
        return githubMock;
    }

    @Bean(name = "openaiMock")
    public WireMockServer openaiMock() {
        openaiMock = new WireMockServer(WireMockConfiguration.options()
                .dynamicPort()
                .usingFilesUnderClasspath("wiremock/openai"));
        openaiMock.start();
        return openaiMock;
    }

    @Bean(name = "anthropicMock")
    public WireMockServer anthropicMock() {
        anthropicMock = new WireMockServer(WireMockConfiguration.options()
                .dynamicPort()
                .usingFilesUnderClasspath("wiremock/anthropic"));
        anthropicMock.start();
        return anthropicMock;
    }

    @Bean(name = "openrouterMock")
    public WireMockServer openrouterMock() {
        openrouterMock = new WireMockServer(WireMockConfiguration.options()
                .dynamicPort()
                .usingFilesUnderClasspath("wiremock/openrouter"));
        openrouterMock.start();
        return openrouterMock;
    }

    @Bean(name = "ragPipelineMock")
    public WireMockServer ragPipelineMock() {
        ragPipelineMock = new WireMockServer(WireMockConfiguration.options()
                .dynamicPort()
                .usingFilesUnderClasspath("wiremock/rag-pipeline"));
        ragPipelineMock.start();
        return ragPipelineMock;
    }

    @PreDestroy
    public void stopWireMockServers() {
        stopIfRunning(bitbucketCloudMock);
        stopIfRunning(bitbucketServerMock);
        stopIfRunning(gitlabMock);
        stopIfRunning(githubMock);
        stopIfRunning(openaiMock);
        stopIfRunning(anthropicMock);
        stopIfRunning(openrouterMock);
        stopIfRunning(ragPipelineMock);
    }

    private void stopIfRunning(WireMockServer server) {
        if (server != null && server.isRunning()) {
            server.stop();
        }
    }
}
