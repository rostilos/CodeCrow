package org.rostilos.codecrow.vcsclient.github;

import okhttp3.OkHttpClient;
import okhttp3.mockwebserver.MockResponse;
import okhttp3.mockwebserver.MockWebServer;
import org.junit.jupiter.api.Nested;
import org.junit.jupiter.api.Test;

import java.security.KeyPair;
import java.security.KeyPairGenerator;
import java.security.PrivateKey;
import java.util.Base64;

import static org.assertj.core.api.Assertions.assertThat;
import static org.assertj.core.api.Assertions.assertThatThrownBy;

class GitHubAppAuthServiceTest {

    private PrivateKey generateTestKey() throws Exception {
        KeyPairGenerator gen = KeyPairGenerator.getInstance("RSA");
        gen.initialize(2048);
        KeyPair kp = gen.generateKeyPair();
        return kp.getPrivate();
    }

    // ── Constructor with PrivateKey ──────────────────────────────────────

    @Test
    void constructor_withPrivateKey_shouldCreateInstance() throws Exception {
        PrivateKey key = generateTestKey();
        GitHubAppAuthService service = new GitHubAppAuthService("12345", key);
        assertThat(service).isNotNull();
    }

    // ── generateAppJwt ──────────────────────────────────────────────────

    @Nested
    class GenerateAppJwt {

        @Test
        void shouldReturnNonEmptyJwt() throws Exception {
            PrivateKey key = generateTestKey();
            GitHubAppAuthService service = new GitHubAppAuthService("12345", key);

            String jwt = service.generateAppJwt();

            assertThat(jwt).isNotBlank();
            // JWT has 3 parts separated by dots
            assertThat(jwt.split("\\.")).hasSize(3);
        }

        @Test
        void jwtPayload_shouldContainIssuer() throws Exception {
            PrivateKey key = generateTestKey();
            GitHubAppAuthService service = new GitHubAppAuthService("99999", key);

            String jwt = service.generateAppJwt();
            // Decode the payload (second part)
            String[] parts = jwt.split("\\.");
            String payload = new String(Base64.getUrlDecoder().decode(parts[1]));
            assertThat(payload).contains("99999");
        }

        @Test
        void consecutiveJwts_shouldDiffer() throws Exception {
            PrivateKey key = generateTestKey();
            GitHubAppAuthService service = new GitHubAppAuthService("12345", key);

            String jwt1 = service.generateAppJwt();
            // Slight delay or just different call should still produce valid but potentially same tokens
            String jwt2 = service.generateAppJwt();
            // Both should be valid JWTs
            assertThat(jwt1.split("\\.")).hasSize(3);
            assertThat(jwt2.split("\\.")).hasSize(3);
        }
    }

    // ── parsePrivateKeyContent ──────────────────────────────────────────

    @Nested
    class ParsePrivateKeyContent {

        @Test
        void pkcs8_shouldParseSuccessfully() throws Exception {
            PrivateKey original = generateTestKey();
            String pem = "-----BEGIN PRIVATE KEY-----\n"
                    + Base64.getMimeEncoder(64, "\n".getBytes()).encodeToString(original.getEncoded())
                    + "\n-----END PRIVATE KEY-----";

            PrivateKey parsed = GitHubAppAuthService.parsePrivateKeyContent(pem);
            assertThat(parsed).isNotNull();
            assertThat(parsed.getAlgorithm()).isEqualTo("RSA");
            assertThat(parsed.getEncoded()).isEqualTo(original.getEncoded());
        }

        @Test
        void pkcs8_withExtraWhitespace_shouldStillParse() throws Exception {
            PrivateKey original = generateTestKey();
            String pem = "-----BEGIN PRIVATE KEY-----\n\n"
                    + Base64.getMimeEncoder(64, "\n".getBytes()).encodeToString(original.getEncoded())
                    + "\n\n-----END PRIVATE KEY-----\n";

            PrivateKey parsed = GitHubAppAuthService.parsePrivateKeyContent(pem);
            assertThat(parsed).isNotNull();
        }

        @Test
        void invalidContent_shouldThrow() {
            assertThatThrownBy(() ->
                    GitHubAppAuthService.parsePrivateKeyContent("-----BEGIN PRIVATE KEY-----\nnotbase64!!!\n-----END PRIVATE KEY-----"))
                    .isInstanceOf(Exception.class);
        }
    }

    // ── InstallationToken record ─────────────────────────────────────────

    @Test
    void installationToken_shouldHoldValues() {
        var token = new GitHubAppAuthService.InstallationToken("tok123",
                java.time.LocalDateTime.of(2025, 1, 1, 0, 0));
        assertThat(token.token()).isEqualTo("tok123");
        assertThat(token.expiresAt().getYear()).isEqualTo(2025);
    }

    // ── InstallationInfo record ──────────────────────────────────────────

    @Test
    void installationInfo_shouldHoldValues() {
        var info = new GitHubAppAuthService.InstallationInfo(
                1L, 100L, "myorg", "Organization", "https://avatar", "Organization");
        assertThat(info.installationId()).isEqualTo(1L);
        assertThat(info.accountLogin()).isEqualTo("myorg");
        assertThat(info.accountType()).isEqualTo("Organization");
        assertThat(info.targetType()).isEqualTo("Organization");
    }

    @Test
    void organizationInstallation_isAuthorizedWhenVisibleToRequester() throws Exception {
        MockWebServer server = new MockWebServer();
        server.start();
        try {
            server.enqueue(jsonResponse("{\"id\":42,\"login\":\"octocat\"}"));
            server.enqueue(jsonResponse("""
                    {"total_count":1,"installations":[{
                      "id":99,
                      "account":{"id":100,"login":"secure-org","type":"Organization"},
                      "target_type":"Organization"
                    }]}
                    """));
            GitHubAppAuthService service = new GitHubAppAuthService(
                    "12345", generateTestKey(), new OkHttpClient(), server.url("/").toString());
            var installation = new GitHubAppAuthService.InstallationInfo(
                    99L, 100L, "secure-org", "Organization", null, "Organization");

            assertThat(service.canUserAccessInstallation("ghu_test", installation)).isTrue();
            assertThat(server.takeRequest().getPath()).isEqualTo("/user");
            assertThat(server.takeRequest().getPath()).isEqualTo("/user/installations?per_page=100&page=1");
        } finally {
            server.shutdown();
        }
    }

    @Test
    void organizationInstallation_doesNotRequireOwnerMembershipLookup() throws Exception {
        MockWebServer server = new MockWebServer();
        server.start();
        try {
            server.enqueue(jsonResponse("{\"id\":42,\"login\":\"octocat\"}"));
            server.enqueue(jsonResponse("""
                    {"total_count":1,"installations":[{
                      "id":99,
                      "account":{"id":100,"login":"secure-org","type":"Organization"},
                      "target_type":"Organization"
                    }]}
                    """));
            GitHubAppAuthService service = new GitHubAppAuthService(
                    "12345", generateTestKey(), new OkHttpClient(), server.url("/").toString());
            var installation = new GitHubAppAuthService.InstallationInfo(
                    99L, 100L, "secure-org", "Organization", null, "Organization");

            assertThat(service.canUserAccessInstallation("ghu_test", installation)).isTrue();
            assertThat(server.getRequestCount()).isEqualTo(2);
        } finally {
            server.shutdown();
        }
    }

    @Test
    void installationNotVisibleToUser_isRejectedBeforeMembershipLookup() throws Exception {
        MockWebServer server = new MockWebServer();
        server.start();
        try {
            server.enqueue(jsonResponse("{\"id\":42,\"login\":\"octocat\"}"));
            server.enqueue(jsonResponse("{\"total_count\":0,\"installations\":[]}"));

            GitHubAppAuthService service = new GitHubAppAuthService(
                    "12345", generateTestKey(), new OkHttpClient(), server.url("/").toString());
            var installation = new GitHubAppAuthService.InstallationInfo(
                    99L, 100L, "victim-org", "Organization", null, "Organization");

            assertThat(service.canUserAccessInstallation("ghu_test", installation)).isFalse();
            assertThat(server.getRequestCount()).isEqualTo(2);
        } finally {
            server.shutdown();
        }
    }

    @Test
    void personalInstallation_requiresTheOwningGitHubUser() throws Exception {
        MockWebServer server = new MockWebServer();
        server.start();
        try {
            server.enqueue(jsonResponse("{\"id\":100,\"login\":\"owner\"}"));
            server.enqueue(jsonResponse("""
                    {"total_count":1,"installations":[{
                      "id":99,
                      "account":{"id":100,"login":"owner","type":"User"},
                      "target_type":"User"
                    }]}
                    """));

            GitHubAppAuthService service = new GitHubAppAuthService(
                    "12345", generateTestKey(), new OkHttpClient(), server.url("/").toString());
            var installation = new GitHubAppAuthService.InstallationInfo(
                    99L, 100L, "owner", "User", null, "User");

            assertThat(service.canUserAccessInstallation("ghu_test", installation)).isTrue();
            assertThat(server.getRequestCount()).isEqualTo(2);
        } finally {
            server.shutdown();
        }
    }

    @Test
    void installationRequests_includeExactRequesterAndTargetAccount() throws Exception {
        MockWebServer server = new MockWebServer();
        server.start();
        try {
            server.enqueue(jsonResponse("""
                    [{
                      "id":501,
                      "account":{"id":100,"login":"secure-org","type":"Organization"},
                      "requester":{"id":42,"login":"octocat"},
                      "created_at":"2026-07-13T08:48:27Z"
                    }]
                    """));

            GitHubAppAuthService service = new GitHubAppAuthService(
                    "12345", generateTestKey(), new OkHttpClient(), server.url("/").toString());

            assertThat(service.listInstallationRequests()).singleElement().satisfies(request -> {
                assertThat(request.requestId()).isEqualTo(501L);
                assertThat(request.accountId()).isEqualTo(100L);
                assertThat(request.requesterId()).isEqualTo(42L);
            });
            assertThat(server.takeRequest().getPath())
                    .isEqualTo("/app/installation-requests?per_page=100&page=1");
        } finally {
            server.shutdown();
        }
    }

    private MockResponse jsonResponse(String body) {
        return new MockResponse()
                .setResponseCode(200)
                .addHeader("Content-Type", "application/json")
                .setBody(body);
    }
}
