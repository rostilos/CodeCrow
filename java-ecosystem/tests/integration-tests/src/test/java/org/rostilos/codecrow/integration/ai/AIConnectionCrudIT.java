package org.rostilos.codecrow.integration.ai;

import io.restassured.response.Response;
import org.junit.jupiter.api.*;
import org.rostilos.codecrow.core.model.workspace.Workspace;
import org.rostilos.codecrow.integration.base.BaseIntegrationTest;
import org.rostilos.codecrow.integration.mock.AIProviderMockSetup;

import static io.restassured.RestAssured.given;
import static org.hamcrest.Matchers.*;

/**
 * Integration tests for AI Connection CRUD operations.
 * Tests connections to OpenAI, Anthropic, and OpenRouter.
 */
@TestMethodOrder(MethodOrderer.OrderAnnotation.class)
@Tag("ai")
@Tag("fast")
class AIConnectionCrudIT extends BaseIntegrationTest {

    private AIProviderMockSetup aiMock;
    private Workspace testWorkspace;

    @BeforeEach
    void setUpTest() {
        aiMock = new AIProviderMockSetup(openaiMock, anthropicMock, openrouterMock);
        authTestHelper.initializeTestUsers();
        testWorkspace = authTestHelper.getDefaultWorkspace();
    }

    @Test
    @Order(1)
    @DisplayName("Should create OpenRouter AI connection")
    void shouldCreateOpenRouterConnection() {
        String requestBody = """
            {
                "providerKey": "OPENROUTER",
                "aiModel": "anthropic/claude-3-haiku",
                "apiKey": "test-api-key-openrouter",
                "tokenLimitation": "200000"
            }
            """;

        given()
                .spec(authenticatedAsAdmin())
                .body(requestBody)
                .when()
                .post("/api/{workspaceSlug}/ai/create", testWorkspace.getSlug())
                .then()
                .statusCode(201)
                .body("providerKey", equalTo("OPENROUTER"))
                .body("aiModel", equalTo("anthropic/claude-3-haiku"))
                .body("tokenLimitation", equalTo(200000))
                .body("id", notNullValue());
    }

    @Test
    @Order(2)
    @DisplayName("Should create OpenAI connection")
    void shouldCreateOpenAIConnection() {
        String requestBody = """
            {
                "providerKey": "OPENAI",
                "aiModel": "gpt-4o-mini",
                "apiKey": "test-api-key-openai",
                "tokenLimitation": "128000"
            }
            """;

        given()
                .spec(authenticatedAsAdmin())
                .body(requestBody)
                .when()
                .post("/api/{workspaceSlug}/ai/create", testWorkspace.getSlug())
                .then()
                .statusCode(201)
                .body("providerKey", equalTo("OPENAI"))
                .body("aiModel", equalTo("gpt-4o-mini"));
    }

    @Test
    @Order(3)
    @DisplayName("Should create Anthropic connection")
    void shouldCreateAnthropicConnection() {
        String requestBody = """
            {
                "providerKey": "ANTHROPIC",
                "aiModel": "claude-3-haiku-20240307",
                "apiKey": "test-api-key-anthropic",
                "tokenLimitation": "200000"
            }
            """;

        given()
                .spec(authenticatedAsAdmin())
                .body(requestBody)
                .when()
                .post("/api/{workspaceSlug}/ai/create", testWorkspace.getSlug())
                .then()
                .statusCode(201)
                .body("providerKey", equalTo("ANTHROPIC"))
                .body("aiModel", equalTo("claude-3-haiku-20240307"));
    }

    @Test
    @Order(4)
    @DisplayName("Should list AI connections for workspace")
    void shouldListAIConnections() {
        String requestBody = """
            {
                "providerKey": "OPENROUTER",
                "aiModel": "test-model",
                "apiKey": "test-key",
                "tokenLimitation": "100000"
            }
            """;

        given()
                .spec(authenticatedAsAdmin())
                .body(requestBody)
                .when()
                .post("/api/{workspaceSlug}/ai/create", testWorkspace.getSlug())
                .then()
                .statusCode(201);

        given()
                .spec(authenticatedAsAdmin())
                .when()
                .get("/api/{workspaceSlug}/ai/list", testWorkspace.getSlug())
                .then()
                .statusCode(200)
                .body("$", hasSize(greaterThanOrEqualTo(1)));
    }

    @Test
    @Order(5)
    @DisplayName("Should update AI connection")
    void shouldUpdateAIConnection() {
        String createBody = """
            {
                "providerKey": "OPENROUTER",
                "aiModel": "original-model",
                "apiKey": "original-key",
                "tokenLimitation": "100000"
            }
            """;

        Long connectionId = given()
                .spec(authenticatedAsAdmin())
                .body(createBody)
                .when()
                .post("/api/{workspaceSlug}/ai/create", testWorkspace.getSlug())
                .then()
                .statusCode(201)
                .extract().jsonPath().getLong("id");

        String updateBody = """
            {
                "providerKey": "OPENROUTER",
                "aiModel": "updated-model",
                "apiKey": "updated-key",
                "tokenLimitation": "150000"
            }
            """;

        given()
                .spec(authenticatedAsAdmin())
                .body(updateBody)
                .when()
                .patch("/api/{workspaceSlug}/ai/{connectionId}", testWorkspace.getSlug(), connectionId)
                .then()
                .statusCode(200)
                .body("aiModel", equalTo("updated-model"))
                .body("tokenLimitation", equalTo(150000));
    }

    @Test
    @Order(6)
    @DisplayName("Should delete AI connection")
    void shouldDeleteAIConnection() {
        String createBody = """
            {
                "providerKey": "OPENROUTER",
                "aiModel": "to-delete",
                "apiKey": "delete-key",
                "tokenLimitation": "100000"
            }
            """;

        Long connectionId = given()
                .spec(authenticatedAsAdmin())
                .body(createBody)
                .when()
                .post("/api/{workspaceSlug}/ai/create", testWorkspace.getSlug())
                .then()
                .statusCode(201)
                .extract().jsonPath().getLong("id");

        given()
                .spec(authenticatedAsAdmin())
                .when()
                .delete("/api/{workspaceSlug}/ai/connections/{connectionId}", testWorkspace.getSlug(), connectionId)
                .then()
                .statusCode(204);
    }

    @Test
    @Order(7)
    @DisplayName("Should require admin rights for AI connection operations")
    void shouldRequireAdminRightsForAIOperations() {
        String requestBody = """
            {
                "providerKey": "OPENROUTER",
                "aiModel": "test-model",
                "apiKey": "test-key",
                "tokenLimitation": "100000"
            }
            """;

        Response response = given()
                .spec(authenticatedAsUser())
                .body(requestBody)
                .when()
                .post("/api/{workspaceSlug}/ai/create", testWorkspace.getSlug());
        
        System.out.println("Admin rights test - Status: " + response.getStatusCode());
        
        response.then()
                .statusCode(anyOf(equalTo(403), equalTo(500)));
    }

    @Test
    @Order(8)
    @DisplayName("Should allow members to list AI connections")
    void shouldAllowMembersToListConnections() {
        given()
                .spec(authenticatedAsUser())
                .when()
                .get("/api/{workspaceSlug}/ai/list", testWorkspace.getSlug())
                .then()
                .statusCode(200);
    }

    @Test
    @Order(9)
    @DisplayName("Should validate required fields")
    void shouldValidateRequiredFields() {
        String invalidBody = """
            {
                "providerKey": "OPENROUTER"
            }
            """;

        Response response = given()
                .spec(authenticatedAsAdmin())
                .body(invalidBody)
                .when()
                .post("/api/{workspaceSlug}/ai/create", testWorkspace.getSlug());
        
        System.out.println("Validation test - Status: " + response.getStatusCode());
        System.out.println("Validation test - Body: " + response.getBody().asString());
        
        response.then()
                .statusCode(anyOf(equalTo(400), equalTo(500)));
    }

    @Test
    @Order(10)
    @DisplayName("Should validate provider key values")
    void shouldValidateProviderKey() {
        String invalidBody = """
            {
                "providerKey": "INVALID_PROVIDER",
                "aiModel": "test-model",
                "apiKey": "test-key",
                "tokenLimitation": "100000"
            }
            """;

        Response response = given()
                .spec(authenticatedAsAdmin())
                .body(invalidBody)
                .when()
                .post("/api/{workspaceSlug}/ai/create", testWorkspace.getSlug());
        
        System.out.println("Invalid provider test - Status: " + response.getStatusCode());
        System.out.println("Invalid provider test - Body: " + response.getBody().asString());
        
        response.then()
                .statusCode(anyOf(equalTo(400), equalTo(500)));
    }

    @Test
    @Order(11)
    @DisplayName("Should prevent cross-workspace AI connection access")
    void shouldPreventCrossWorkspaceAccess() {
        Workspace otherWorkspace = authTestHelper.createTestWorkspace("AI Other Workspace", authTestHelper.getAdminUser());

        String createBody = """
            {
                "providerKey": "OPENROUTER",
                "aiModel": "other-ws-model",
                "apiKey": "other-ws-key",
                "tokenLimitation": "100000"
            }
            """;

        Long connectionId = given()
                .spec(authenticatedAsAdmin())
                .body(createBody)
                .when()
                .post("/api/{workspaceSlug}/ai/create", otherWorkspace.getSlug())
                .then()
                .statusCode(201)
                .extract().jsonPath().getLong("id");

        Response response = given()
                .spec(authenticatedAsAdmin())
                .when()
                .delete("/api/{workspaceSlug}/ai/connections/{connectionId}", testWorkspace.getSlug(), connectionId);
        
        System.out.println("Cross-workspace test - Status: " + response.getStatusCode());
        
        response.then()
                .statusCode(anyOf(equalTo(400), equalTo(401), equalTo(404), equalTo(500)));
    }
}
