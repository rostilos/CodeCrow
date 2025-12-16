package org.rostilos.codecrow.integration.base;

import com.fasterxml.jackson.databind.ObjectMapper;
import com.github.tomakehurst.wiremock.WireMockServer;
import io.restassured.RestAssured;
import io.restassured.builder.RequestSpecBuilder;
import io.restassured.specification.RequestSpecification;
import org.junit.jupiter.api.BeforeAll;
import org.junit.jupiter.api.BeforeEach;
import org.junit.jupiter.api.TestInstance;
import org.rostilos.codecrow.integration.IntegrationTestApplication;
import org.rostilos.codecrow.integration.config.TestContainersInitializer;
import org.rostilos.codecrow.integration.config.TestCredentialsConfig;
import org.rostilos.codecrow.integration.config.TestEmailConfig;
import org.rostilos.codecrow.integration.config.WireMockConfig;
import org.rostilos.codecrow.integration.util.AuthTestHelper;
import org.rostilos.codecrow.integration.util.TestDataCleaner;
import org.springframework.beans.factory.annotation.Autowired;
import org.springframework.beans.factory.annotation.Qualifier;
import org.springframework.boot.test.context.SpringBootTest;
import org.springframework.boot.test.web.server.LocalServerPort;
import org.springframework.context.annotation.Import;
import org.springframework.test.context.ActiveProfiles;
import org.springframework.test.context.ContextConfiguration;

/**
 * Base class for all integration tests.
 * Provides common setup for:
 * - Spring Boot test context
 * - TestContainers (PostgreSQL)
 * - WireMock servers for external services
 * - REST Assured configuration
 * - Authentication helpers
 * - Test data cleanup
 */
@SpringBootTest(
        classes = IntegrationTestApplication.class,
        webEnvironment = SpringBootTest.WebEnvironment.RANDOM_PORT
)
@ContextConfiguration(initializers = TestContainersInitializer.class)
@Import({WireMockConfig.class, TestEmailConfig.class})
@ActiveProfiles("test")
@TestInstance(TestInstance.Lifecycle.PER_CLASS)
public abstract class BaseIntegrationTest {

    @LocalServerPort
    protected int port;

    @Autowired
    protected ObjectMapper objectMapper;

    @Autowired
    protected TestDataCleaner testDataCleaner;

    @Autowired
    protected AuthTestHelper authTestHelper;

    @Autowired(required = false)
    protected TestCredentialsConfig testCredentials;

    @Autowired
    @Qualifier("bitbucketCloudMock")
    protected WireMockServer bitbucketCloudMock;

    @Autowired
    @Qualifier("bitbucketServerMock")
    protected WireMockServer bitbucketServerMock;

    @Autowired
    @Qualifier("gitlabMock")
    protected WireMockServer gitlabMock;

    @Autowired
    @Qualifier("githubMock")
    protected WireMockServer githubMock;

    @Autowired
    @Qualifier("openaiMock")
    protected WireMockServer openaiMock;

    @Autowired
    @Qualifier("anthropicMock")
    protected WireMockServer anthropicMock;

    @Autowired
    @Qualifier("openrouterMock")
    protected WireMockServer openrouterMock;

    @Autowired
    @Qualifier("ragPipelineMock")
    protected WireMockServer ragPipelineMock;

    protected RequestSpecification requestSpec;

    @BeforeAll
    void setUpAll() {
        RestAssured.enableLoggingOfRequestAndResponseIfValidationFails();
    }

    @BeforeEach
    void setUp() {
        requestSpec = new RequestSpecBuilder()
                .setBaseUri("http://localhost")
                .setPort(port)
                .setContentType("application/json")
                .build();
        
        resetWireMockServers();
    }

    protected void resetWireMockServers() {
        bitbucketCloudMock.resetAll();
        bitbucketServerMock.resetAll();
        gitlabMock.resetAll();
        githubMock.resetAll();
        openaiMock.resetAll();
        anthropicMock.resetAll();
        openrouterMock.resetAll();
        ragPipelineMock.resetAll();
    }

    protected RequestSpecification authenticatedRequest(String token) {
        return new RequestSpecBuilder()
                .addRequestSpecification(requestSpec)
                .addHeader("Authorization", "Bearer " + token)
                .build();
    }

    protected RequestSpecification authenticatedAsAdmin() {
        String adminToken = authTestHelper.getAdminToken();
        return authenticatedRequest(adminToken);
    }

    protected RequestSpecification authenticatedAsUser() {
        String userToken = authTestHelper.getUserToken();
        return authenticatedRequest(userToken);
    }

    protected RequestSpecification authenticatedAs(String username, String password) {
        String token = authTestHelper.loginAndGetToken(username, password);
        return authenticatedRequest(token);
    }

    protected String getBitbucketCloudMockUrl() {
        return "http://localhost:" + bitbucketCloudMock.port();
    }

    protected String getBitbucketServerMockUrl() {
        return "http://localhost:" + bitbucketServerMock.port();
    }

    protected String getGitLabMockUrl() {
        return "http://localhost:" + gitlabMock.port();
    }

    protected String getGitHubMockUrl() {
        return "http://localhost:" + githubMock.port();
    }

    protected String getOpenAIMockUrl() {
        return "http://localhost:" + openaiMock.port();
    }

    protected String getAnthropicMockUrl() {
        return "http://localhost:" + anthropicMock.port();
    }

    protected String getOpenRouterMockUrl() {
        return "http://localhost:" + openrouterMock.port();
    }

    protected String getRagPipelineMockUrl() {
        return "http://localhost:" + ragPipelineMock.port();
    }
}
