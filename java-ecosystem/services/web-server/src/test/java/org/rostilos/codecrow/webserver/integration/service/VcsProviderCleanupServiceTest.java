package org.rostilos.codecrow.webserver.integration.service;

import okhttp3.Call;
import okhttp3.MediaType;
import okhttp3.OkHttpClient;
import okhttp3.Protocol;
import okhttp3.Request;
import okhttp3.Response;
import okhttp3.ResponseBody;
import okhttp3.mockwebserver.MockResponse;
import okhttp3.mockwebserver.MockWebServer;
import okio.Buffer;
import org.junit.jupiter.api.BeforeEach;
import org.junit.jupiter.api.Test;
import org.junit.jupiter.api.extension.ExtendWith;
import org.mockito.ArgumentCaptor;
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

import java.io.IOException;
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
    @Mock private Call call;

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
        VcsConnection connection = appConnection(EVcsProvider.GITLAB);
        connection.setAccessToken("encrypted-token");
        connection.setConfiguration(new GitLabConfig(
                null, null, null, "https://gitlab.connection.example/"));
        when(encryptionService.decrypt("encrypted-token")).thenReturn("plain-token");
        when(siteSettingsProvider.getGitLabSettings()).thenReturn(
                new GitLabSettingsDTO(
                        "client-id",
                        "client-secret",
                        "https://gitlab.example/"));
        when(httpClient.newCall(any(Request.class))).thenReturn(call);
        when(call.execute()).thenAnswer(invocation -> response(200));

        service.removeProviderAuthorization(connection);

        ArgumentCaptor<Request> request = ArgumentCaptor.forClass(Request.class);
        verify(httpClient).newCall(request.capture());
        assertThat(request.getValue().method()).isEqualTo("POST");
        assertThat(request.getValue().url().toString())
                .isEqualTo("https://gitlab.connection.example/oauth/revoke");
        Buffer body = new Buffer();
        request.getValue().body().writeTo(body);
        assertThat(body.readUtf8())
                .contains("client_id=client-id")
                .contains("client_secret=client-secret")
                .contains("token=plain-token");
    }

    @Test
    void failedGitLabRevokeKeepsDeletionRetryable() throws Exception {
        VcsConnection connection = appConnection(EVcsProvider.GITLAB);
        connection.setAccessToken("encrypted-token");
        when(encryptionService.decrypt("encrypted-token")).thenReturn("plain-token");
        when(siteSettingsProvider.getGitLabSettings()).thenReturn(
                new GitLabSettingsDTO(
                        "client-id",
                        "client-secret",
                        "https://gitlab.example"));
        when(httpClient.newCall(any(Request.class))).thenReturn(call);
        when(call.execute()).thenAnswer(invocation -> response(503));

        assertThatThrownBy(() -> service.removeProviderAuthorization(connection))
                .isInstanceOf(IntegrationException.class)
                .hasMessageContaining("kept so deletion can be retried");
    }

    @Test
    void legacyGitLabOAuthConnectionStillRevokesOnGitLabCom() throws Exception {
        VcsConnection connection = appConnection(EVcsProvider.GITLAB);
        connection.setAccessToken("encrypted-token");
        when(encryptionService.decrypt("encrypted-token")).thenReturn("plain-token");
        when(siteSettingsProvider.getGitLabSettings()).thenReturn(
                new GitLabSettingsDTO(
                        "client-id",
                        "client-secret",
                        "https://new-self-managed.example"));
        when(httpClient.newCall(any(Request.class))).thenReturn(call);
        when(call.execute()).thenAnswer(invocation -> response(200));

        service.removeProviderAuthorization(connection);

        ArgumentCaptor<Request> request = ArgumentCaptor.forClass(Request.class);
        verify(httpClient).newCall(request.capture());
        assertThat(request.getValue().url().toString())
                .isEqualTo("https://gitlab.com/oauth/revoke");
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

    private Response response(int status) throws IOException {
        Request request = new Request.Builder()
                .url("https://gitlab.example/oauth/revoke")
                .build();
        return new Response.Builder()
                .request(request)
                .protocol(Protocol.HTTP_1_1)
                .message("test")
                .code(status)
                .body(ResponseBody.create("", MediaType.get("text/plain")))
                .build();
    }
}
