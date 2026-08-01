package org.rostilos.codecrow.webserver.integration.service;

import okhttp3.OkHttpClient;
import okhttp3.mockwebserver.MockResponse;
import okhttp3.mockwebserver.MockWebServer;
import org.junit.jupiter.api.BeforeEach;
import org.junit.jupiter.api.Test;
import org.junit.jupiter.api.extension.ExtendWith;
import org.mockito.Mock;
import org.mockito.junit.jupiter.MockitoExtension;
import org.rostilos.codecrow.core.dto.admin.GitLabSettingsDTO;
import org.rostilos.codecrow.core.model.vcs.BitbucketConnectInstallation;
import org.rostilos.codecrow.core.model.vcs.EVcsConnectionType;
import org.rostilos.codecrow.core.model.vcs.EVcsProvider;
import org.rostilos.codecrow.core.model.vcs.EVcsSetupStatus;
import org.rostilos.codecrow.core.model.vcs.VcsConnection;
import org.rostilos.codecrow.core.model.vcs.config.gitlab.GitLabConfig;
import org.rostilos.codecrow.core.persistence.repository.vcs.BitbucketConnectInstallationRepository;
import org.rostilos.codecrow.core.persistence.repository.vcs.VcsConnectionRepository;
import org.rostilos.codecrow.core.service.SiteSettingsProvider;
import org.rostilos.codecrow.security.oauth.TokenEncryptionService;
import org.rostilos.codecrow.webserver.exception.IntegrationException;

import java.nio.charset.StandardCharsets;
import java.security.MessageDigest;
import java.util.HexFormat;
import java.util.Optional;

import static org.assertj.core.api.Assertions.assertThat;
import static org.assertj.core.api.Assertions.assertThatThrownBy;
import static org.mockito.ArgumentMatchers.any;
import static org.mockito.Mockito.never;
import static org.mockito.Mockito.verify;
import static org.mockito.Mockito.verifyNoInteractions;
import static org.mockito.Mockito.when;

@ExtendWith(MockitoExtension.class)
class VcsProviderCleanupServiceTest {

    @Mock private SiteSettingsProvider siteSettingsProvider;
    @Mock private TokenEncryptionService encryptionService;
    @Mock private BitbucketConnectInstallationRepository connectInstallationRepository;
    @Mock private VcsConnectionRepository connectionRepository;
    @Mock private OkHttpClient httpClient;

    private VcsProviderCleanupService service;

    @BeforeEach
    void setUp() {
        service = new VcsProviderCleanupService(
                siteSettingsProvider,
                encryptionService,
                connectInstallationRepository,
                connectionRepository,
                httpClient);
    }

    @Test
    void revokesTheExactGitLabOAuthToken() throws Exception {
        try (MockWebServer gitLab = new MockWebServer()) {
            gitLab.enqueue(new MockResponse().setResponseCode(200));
            gitLab.start();
            service = new VcsProviderCleanupService(
                    siteSettingsProvider,
                    encryptionService,
                    connectInstallationRepository,
                    connectionRepository,
                    new OkHttpClient());
            VcsConnection connection = appConnection(EVcsProvider.GITLAB);
            connection.setAccessToken("encrypted-token");
            connection.setConfiguration(new GitLabConfig(
                    null, null, null, gitLab.url("/").toString()));
            when(encryptionService.decrypt("encrypted-token")).thenReturn("plain-token");
            when(siteSettingsProvider.getGitLabSettings()).thenReturn(
                    new GitLabSettingsDTO(
                            "client-id",
                            "client-secret",
                            gitLab.url("/").toString()));

            service.removeProviderAuthorization(connection);

            var request = gitLab.takeRequest();
            assertThat(request.getMethod()).isEqualTo("POST");
            assertThat(request.getPath()).isEqualTo("/oauth/revoke");
            assertThat(request.getBody().readUtf8())
                    .contains("client_id=client-id")
                    .contains("client_secret=client-secret")
                    .contains("token=plain-token");
        }
    }

    @Test
    void failedGitLabRevokeKeepsDeletionRetryable() throws Exception {
        try (MockWebServer gitLab = new MockWebServer()) {
            gitLab.enqueue(new MockResponse().setResponseCode(503));
            gitLab.start();
            service = new VcsProviderCleanupService(
                    siteSettingsProvider,
                    encryptionService,
                    connectInstallationRepository,
                    connectionRepository,
                    new OkHttpClient());
            VcsConnection connection = appConnection(EVcsProvider.GITLAB);
            connection.setAccessToken("encrypted-token");
            connection.setConfiguration(new GitLabConfig(
                    null, null, null, gitLab.url("/").toString()));
            when(encryptionService.decrypt("encrypted-token")).thenReturn("plain-token");
            when(siteSettingsProvider.getGitLabSettings()).thenReturn(
                    new GitLabSettingsDTO(
                            "client-id",
                            "client-secret",
                            gitLab.url("/").toString()));

            assertThatThrownBy(() -> service.removeProviderAuthorization(connection))
                    .isInstanceOf(IntegrationException.class)
                    .hasMessageContaining("kept so deletion can be retried");
        }
    }

    @Test
    void mismatchedGitLabIssuerNeverReceivesTheGlobalSecret() throws Exception {
        VcsConnection connection = appConnection(EVcsProvider.GITLAB);
        connection.setAccessToken("encrypted-token");
        connection.setConfiguration(new GitLabConfig(
                null, null, null, "https://attacker.example"));
        when(siteSettingsProvider.getGitLabSettings()).thenReturn(
                new GitLabSettingsDTO(
                        "client-id",
                        "client-secret",
                        "https://gitlab.example"));

        assertThatThrownBy(() -> service.removeProviderAuthorization(connection))
                .isInstanceOf(IntegrationException.class)
                .hasMessageContaining("does not match the configured OAuth issuer");
        verify(encryptionService, never()).decrypt(any());
        verifyNoInteractions(httpClient);
    }

    @Test
    void pendingGitHubAccountIdIsNeverUsedAsAnInstallationId() {
        VcsConnection connection = appConnection(EVcsProvider.GITHUB);
        connection.setSetupStatus(EVcsSetupStatus.PENDING);
        connection.setInstallationId(null);
        connection.setExternalWorkspaceId("123456");

        service.removeProviderAuthorization(connection);

        verifyNoInteractions(
                siteSettingsProvider,
                encryptionService,
                connectInstallationRepository,
                httpClient);
    }

    @Test
    void numericGitHubOAuthUsernameIsNeverUsedAsAnInstallationId() {
        VcsConnection connection = appConnection(EVcsProvider.GITHUB);
        connection.setInstallationId(null);
        connection.setExternalWorkspaceId("123456");
        connection.setExternalWorkspaceSlug("123456");

        service.removeProviderAuthorization(connection);

        verifyNoInteractions(
                siteSettingsProvider,
                encryptionService,
                connectInstallationRepository,
                httpClient);
    }

    @Test
    void sharedGitHubInstallationIsKeptUntilItsLastLocalConnectionIsDeleted() {
        VcsConnection connection = appConnection(EVcsProvider.GITHUB);
        connection.setInstallationId("149447866");
        VcsConnection otherWorkspaceConnection = appConnection(EVcsProvider.GITHUB);
        otherWorkspaceConnection.setId(32L);
        otherWorkspaceConnection.setInstallationId("149447866");
        when(connectionRepository.findAllByProviderTypeAndInstallationIdForUpdate(
                EVcsProvider.GITHUB, "149447866"))
                .thenReturn(java.util.List.of(connection, otherWorkspaceConnection));

        service.removeProviderAuthorization(connection);

        verify(connectionRepository).findAllByProviderTypeAndInstallationIdForUpdate(
                EVcsProvider.GITHUB, "149447866");
        verifyNoInteractions(siteSettingsProvider, encryptionService, httpClient);
    }

    @Test
    void deletesTheExactBitbucketConnectInstallationWithItsJwt() throws Exception {
        MockWebServer server = new MockWebServer();
        server.start();
        try {
            server.enqueue(new MockResponse().setResponseCode(204));
            service = new VcsProviderCleanupService(
                    siteSettingsProvider,
                    encryptionService,
                    connectInstallationRepository,
                    connectionRepository,
                    new OkHttpClient(),
                    server.url("/2.0").toString());

            VcsConnection connection = appConnection(EVcsProvider.BITBUCKET_CLOUD);
            BitbucketConnectInstallation installation = new BitbucketConnectInstallation();
            installation.setId(44L);
            installation.setClientKey("client-key-44");
            installation.setSharedSecret("encrypted-shared-secret");
            installation.setEnabled(true);
            String sharedSecret = "0123456789abcdef0123456789abcdef";
            when(connectInstallationRepository.findByVcsConnection_Id(17L))
                    .thenReturn(Optional.of(installation));
            when(encryptionService.decrypt("encrypted-shared-secret"))
                    .thenReturn(sharedSecret);

            service.removeProviderAuthorization(connection);

            var request = server.takeRequest();
            assertThat(request.getMethod()).isEqualTo("DELETE");
            assertThat(request.getPath()).isEqualTo("/2.0/addon");
            assertThat(request.getHeader("Authorization")).startsWith("JWT ");

            String token = request.getHeader("Authorization").substring(4);
            var claims = io.jsonwebtoken.Jwts.parserBuilder()
                    .setSigningKey(io.jsonwebtoken.security.Keys.hmacShaKeyFor(
                            sharedSecret.getBytes(StandardCharsets.UTF_8)))
                    .build()
                    .parseClaimsJws(token)
                    .getBody();
            assertThat(claims.getIssuer()).isEqualTo("client-key-44");
            assertThat(claims.get("qsh", String.class)).isEqualTo(
                    HexFormat.of().formatHex(
                            MessageDigest.getInstance("SHA-256")
                                    .digest("DELETE&/2.0/addon&"
                                            .getBytes(StandardCharsets.UTF_8))));
            assertThat(installation.isEnabled()).isFalse();
            verify(connectInstallationRepository).save(installation);
        } finally {
            server.shutdown();
        }
    }

    @Test
    void manualConnectionsDoNotRevokeProviderAuthorization() throws Exception {
        VcsConnection connection = appConnection(EVcsProvider.GITLAB);
        connection.setConnectionType(EVcsConnectionType.REPOSITORY_TOKEN);
        connection.setAccessToken("encrypted-token");

        service.removeProviderAuthorization(connection);

        verify(encryptionService, never()).decrypt(any());
        verifyNoInteractions(
                siteSettingsProvider,
                connectInstallationRepository,
                httpClient);
    }

    private VcsConnection appConnection(EVcsProvider provider) {
        VcsConnection connection = new VcsConnection();
        connection.setId(17L);
        connection.setProviderType(provider);
        connection.setConnectionType(EVcsConnectionType.APP);
        connection.setSetupStatus(EVcsSetupStatus.CONNECTED);
        return connection;
    }
}
