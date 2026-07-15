package org.rostilos.codecrow.webserver;

import com.fasterxml.jackson.databind.ObjectMapper;
import io.restassured.RestAssured;
import io.restassured.http.ContentType;
import io.restassured.specification.RequestSpecification;
import org.junit.jupiter.api.AfterAll;
import org.junit.jupiter.api.BeforeAll;
import org.junit.jupiter.api.BeforeEach;
import org.rostilos.codecrow.core.persistence.repository.user.UserRepository;
import org.rostilos.codecrow.security.jwt.utils.JwtUtils;
import org.rostilos.codecrow.testsupport.base.IntegrationTest;
import org.rostilos.codecrow.testsupport.cleanup.DatabaseCleaner;
import org.rostilos.codecrow.testsupport.legacy.LegacyContainerItSession;
import org.springframework.beans.factory.annotation.Autowired;
import org.springframework.beans.factory.annotation.Value;
import org.springframework.boot.test.context.SpringBootTest;
import org.springframework.boot.test.web.server.LocalServerPort;
import org.springframework.context.annotation.Import;
import org.springframework.security.crypto.password.PasswordEncoder;

/**
 * Base class for web-server integration tests.
 *
 * <p>Starts the full Spring Boot application context with the guarded
 * PostgreSQL endpoint. Provides REST Assured configuration, JWT helper
 * methods, and database cleanup between tests.</p>
 *
 * <p>Usage: extend this class and write test methods. The database is
 * cleaned before each test via {@link DatabaseCleaner}.</p>
 */
@IntegrationTest
@SpringBootTest(
    classes = WebserverApplication.class,
    webEnvironment = SpringBootTest.WebEnvironment.RANDOM_PORT
)
@Import(DatabaseCleaner.class)
public abstract class BaseWebServerIT {

    private AutoCloseable applicationLoopbackLease;

    @LocalServerPort
    protected int port;

    @Autowired
    protected ObjectMapper objectMapper;

    @Autowired
    protected UserRepository userRepository;

    @Autowired
    protected PasswordEncoder passwordEncoder;

    @Autowired
    protected JwtUtils jwtUtils;

    @Autowired
    protected DatabaseCleaner databaseCleaner;

    @Value("${codecrow.internal.api.secret}")
    protected String internalApiSecret;

    @BeforeAll
    void setupRestAssured() {
        applicationLoopbackLease =
                LegacyContainerItSession.registerApplicationLoopback(port);
        RestAssured.baseURI = "http://127.0.0.1";
        RestAssured.port = port;
        RestAssured.basePath = "";
        RestAssured.enableLoggingOfRequestAndResponseIfValidationFails();
    }

    @AfterAll
    void releaseApplicationLoopback() throws Exception {
        if (applicationLoopbackLease != null) {
            applicationLoopbackLease.close();
            applicationLoopbackLease = null;
        }
    }

    @BeforeEach
    void cleanDatabase() {
        databaseCleaner.cleanAll();
        RestAssured.basePath = "";
    }

    // ── Helper: authenticated request ────────────────────────

    /**
     * Create a REST Assured request spec with a valid JWT Bearer token
     * for the given username.
     */
    protected RequestSpecification authenticatedRequest(String username) {
        String token = jwtUtils.generateJwtTokenForUser(1L, username);
        return RestAssured.given()
                .contentType(ContentType.JSON)
                .header("Authorization", "Bearer " + token);
    }

    /**
     * Create a REST Assured request spec with the internal API secret header.
     */
    protected RequestSpecification internalRequest() {
        return RestAssured.given()
                .contentType(ContentType.JSON)
                .header("X-Internal-Secret", internalApiSecret);
    }

    /**
     * Create an unauthenticated REST Assured request spec.
     */
    protected RequestSpecification unauthenticatedRequest() {
        return RestAssured.given()
                .contentType(ContentType.JSON);
    }

    // ── Helper: register a test user directly in the DB ──────

    /**
     * Create a user directly in the database (bypasses registration endpoint).
     * Returns the saved user's ID.
     */
    protected Long createTestUser(String username, String email, String password) {
        org.rostilos.codecrow.core.model.user.User user =
            new org.rostilos.codecrow.core.model.user.User(
                username, email, passwordEncoder.encode(password), "TestCompany"
            );
        user.setStatus(org.rostilos.codecrow.core.model.user.status.EStatus.STATUS_ACTIVE);
        user.setAccountType(org.rostilos.codecrow.core.model.user.account_type.EAccountType.TYPE_DEFAULT);
        return userRepository.save(user).getId();
    }

    /**
     * Create an admin user directly in the database.
     */
    protected Long createAdminUser(String username, String email, String password) {
        org.rostilos.codecrow.core.model.user.User user =
            new org.rostilos.codecrow.core.model.user.User(
                username, email, passwordEncoder.encode(password), "AdminCorp"
            );
        user.setStatus(org.rostilos.codecrow.core.model.user.status.EStatus.STATUS_ACTIVE);
        user.setAccountType(org.rostilos.codecrow.core.model.user.account_type.EAccountType.TYPE_ADMIN);
        return userRepository.save(user).getId();
    }
}
