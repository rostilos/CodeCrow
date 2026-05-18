package org.rostilos.codecrow.pipelineagent;

import com.fasterxml.jackson.databind.ObjectMapper;
import io.restassured.RestAssured;
import io.restassured.specification.RequestSpecification;
import org.junit.jupiter.api.BeforeAll;
import org.junit.jupiter.api.BeforeEach;
import org.rostilos.codecrow.core.model.project.Project;
import org.rostilos.codecrow.core.model.workspace.Workspace;
import org.rostilos.codecrow.core.persistence.repository.project.ProjectRepository;
import org.rostilos.codecrow.core.persistence.repository.workspace.WorkspaceRepository;
import org.rostilos.codecrow.security.jwt.utils.JwtUtils;
import org.rostilos.codecrow.testsupport.base.IntegrationTest;
import org.rostilos.codecrow.testsupport.cleanup.DatabaseCleaner;
import org.springframework.beans.factory.annotation.Autowired;
import org.springframework.boot.test.context.SpringBootTest;
import org.springframework.boot.test.web.server.LocalServerPort;
import org.springframework.context.annotation.Import;

import jakarta.persistence.EntityManager;
import jakarta.persistence.PersistenceContext;

import static io.restassured.RestAssured.given;

/**
 * Base class for pipeline-agent integration tests.
 * <p>
 * Sets up Testcontainers PostgreSQL, REST Assured,
 * and provides helpers for project-level JWT authentication.
 * </p>
 * <p>
 * Authentication: Pipeline-agent uses project-level JWTs where
 * the JWT subject is the project ID (numeric string). The
 * {@link org.rostilos.codecrow.security.pipelineagent.jwt.ProjectInternalJwtFilter}
 * validates these tokens and loads the Project from the database.
 * </p>
 */
@IntegrationTest
@SpringBootTest(
        classes = ProcessingApplication.class,
        webEnvironment = SpringBootTest.WebEnvironment.RANDOM_PORT
)
@Import(DatabaseCleaner.class)
abstract class BasePipelineAgentIT {

    @LocalServerPort
    protected int port;

    @Autowired
    protected ObjectMapper objectMapper;

    @Autowired
    protected ProjectRepository projectRepository;

    @Autowired
    protected WorkspaceRepository workspaceRepository;

    @Autowired
    protected JwtUtils jwtUtils;

    @Autowired
    protected DatabaseCleaner databaseCleaner;

    @PersistenceContext
    protected EntityManager entityManager;

    @BeforeAll
    void setupRestAssured() {
        RestAssured.port = port;
        RestAssured.enableLoggingOfRequestAndResponseIfValidationFails();
    }

    @BeforeEach
    void cleanDatabase() {
        databaseCleaner.cleanAll();
    }

    // ─── Authentication helpers ──────────────────────────

    /**
     * Create a request with a valid project JWT in the Authorization header.
     *
     * @param projectId the project ID to encode in the JWT subject
     */
    protected RequestSpecification projectAuthRequest(Long projectId) {
        String token = jwtUtils.generateJwtTokenForUser(projectId, String.valueOf(projectId));
        return given()
                .header("Authorization", "Bearer " + token)
                .contentType("application/json");
    }

    /**
     * Create an unauthenticated request (no Authorization header).
     */
    protected RequestSpecification unauthenticatedRequest() {
        return given()
                .contentType("application/json");
    }

    // ─── Test data helpers ───────────────────────────────

    /**
     * Create a workspace and project in the database for testing.
     * Returns the project ID.
     */
    protected Long createTestProject(String namespace, String projectName) {
        // Create workspace first
        Workspace workspace = new Workspace("test-ws", "Test Workspace", "Integration test workspace");
        workspace = workspaceRepository.saveAndFlush(workspace);

        // Create project
        Project project = new Project();
        project.setWorkspace(workspace);
        project.setNamespace(namespace);
        project.setName(projectName);
        project = projectRepository.saveAndFlush(project);

        return project.getId();
    }

    /**
     * Create a workspace and project with a specific workspace slug.
     * Returns the project ID.
     */
    protected Long createTestProject(String wsSlug, String namespace, String projectName) {
        Workspace workspace = new Workspace(wsSlug, "Workspace " + wsSlug, "Test workspace");
        workspace = workspaceRepository.saveAndFlush(workspace);

        Project project = new Project();
        project.setWorkspace(workspace);
        project.setNamespace(namespace);
        project.setName(projectName);
        project = projectRepository.saveAndFlush(project);

        return project.getId();
    }
}
