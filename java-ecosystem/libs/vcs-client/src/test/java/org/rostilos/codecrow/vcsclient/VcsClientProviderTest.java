package org.rostilos.codecrow.vcsclient;

import org.junit.jupiter.api.BeforeEach;
import org.junit.jupiter.api.Nested;
import org.junit.jupiter.api.Test;
import org.junit.jupiter.api.extension.ExtendWith;
import org.mockito.Mock;
import org.mockito.junit.jupiter.MockitoExtension;
import org.rostilos.codecrow.core.model.vcs.EVcsConnectionType;
import org.rostilos.codecrow.core.model.vcs.EVcsProvider;
import org.rostilos.codecrow.core.model.vcs.VcsConnection;
import org.rostilos.codecrow.core.persistence.repository.vcs.BitbucketConnectInstallationRepository;
import org.rostilos.codecrow.core.persistence.repository.vcs.VcsConnectionRepository;
import org.rostilos.codecrow.core.service.SiteSettingsProvider;
import org.rostilos.codecrow.security.oauth.TokenEncryptionService;

import java.lang.reflect.Field;
import java.time.LocalDateTime;

import static org.assertj.core.api.Assertions.assertThat;
import static org.assertj.core.api.Assertions.assertThatThrownBy;
import static org.mockito.Mockito.*;

@ExtendWith(MockitoExtension.class)
class VcsClientProviderTest {

    @Mock private VcsConnectionRepository connectionRepository;
    @Mock private BitbucketConnectInstallationRepository connectInstallationRepository;
    @Mock private TokenEncryptionService encryptionService;
    @Mock private HttpAuthorizedClientFactory httpClientFactory;
    @Mock private SiteSettingsProvider siteSettingsProvider;

    private VcsClientProvider provider;

    private static void setId(Object entity, Long id) throws Exception {
        Field f = entity.getClass().getDeclaredField("id");
        f.setAccessible(true);
        f.set(entity, id);
    }

    @BeforeEach
    void setUp() {
        provider = new VcsClientProvider(
                connectionRepository,
                connectInstallationRepository,
                encryptionService,
                httpClientFactory,
                siteSettingsProvider
        );
    }

    // ── needsTokenRefresh ────────────────────────────────────────────────

    @Nested
    class NeedsTokenRefresh {

        @Test
        void noRefreshToken_nonAppConnection_shouldReturnFalse() {
            VcsConnection conn = new VcsConnection();
            conn.setConnectionType(EVcsConnectionType.PERSONAL_TOKEN);
            conn.setRefreshToken(null);

            assertThat(provider.needsTokenRefresh(conn)).isFalse();
        }

        @Test
        void blankRefreshToken_nonAppConnection_shouldReturnFalse() {
            VcsConnection conn = new VcsConnection();
            conn.setConnectionType(EVcsConnectionType.PERSONAL_TOKEN);
            conn.setRefreshToken("  ");

            assertThat(provider.needsTokenRefresh(conn)).isFalse();
        }

        @Test
        void appConnection_noRefreshToken_expiredToken_shouldReturnTrue() throws Exception {
            VcsConnection conn = new VcsConnection();
            setId(conn, 1L);
            conn.setConnectionType(EVcsConnectionType.APP);
            conn.setRefreshToken(null);
            conn.setTokenExpiresAt(LocalDateTime.now().minusHours(1));

            assertThat(provider.needsTokenRefresh(conn)).isTrue();
        }

        @Test
        void appConnection_noRefreshToken_validToken_shouldReturnFalse() throws Exception {
            VcsConnection conn = new VcsConnection();
            setId(conn, 1L);
            conn.setConnectionType(EVcsConnectionType.APP);
            conn.setRefreshToken(null);
            conn.setTokenExpiresAt(LocalDateTime.now().plusHours(1));

            assertThat(provider.needsTokenRefresh(conn)).isFalse();
        }

        @Test
        void appConnection_noRefreshToken_nullExpiry_shouldReturnFalse() throws Exception {
            VcsConnection conn = new VcsConnection();
            setId(conn, 1L);
            conn.setConnectionType(EVcsConnectionType.APP);
            conn.setRefreshToken(null);
            conn.setTokenExpiresAt(null);

            assertThat(provider.needsTokenRefresh(conn)).isFalse();
        }

        @Test
        void hasRefreshToken_nullExpiry_shouldReturnTrue() {
            VcsConnection conn = new VcsConnection();
            conn.setRefreshToken("some-refresh-token");
            conn.setTokenExpiresAt(null);

            assertThat(provider.needsTokenRefresh(conn)).isTrue();
        }

        @Test
        void hasRefreshToken_expiresWithin5Minutes_shouldReturnTrue() {
            VcsConnection conn = new VcsConnection();
            conn.setRefreshToken("some-refresh-token");
            conn.setTokenExpiresAt(LocalDateTime.now().plusMinutes(2));

            assertThat(provider.needsTokenRefresh(conn)).isTrue();
        }

        @Test
        void hasRefreshToken_expiresInFuture_shouldReturnFalse() {
            VcsConnection conn = new VcsConnection();
            conn.setRefreshToken("some-refresh-token");
            conn.setTokenExpiresAt(LocalDateTime.now().plusHours(1));

            assertThat(provider.needsTokenRefresh(conn)).isFalse();
        }
    }

    // ── evictCachedClient ────────────────────────────────────────────────

    @Test
    void evictCachedClient_null_shouldNotThrow() {
        provider.evictCachedClient(null);
        // No exception
    }

    @Test
    void evictCachedClient_validId_shouldNotThrow() {
        provider.evictCachedClient(42L);
        // No exception even when cache is empty
    }

    // ── getClient ────────────────────────────────────────────────────────

    @Test
    void getClient_connectionWithNoToken_shouldThrowVcsClientException() throws Exception {
        VcsConnection conn = new VcsConnection();
        setId(conn, 1L);
        conn.setProviderType(EVcsProvider.GITHUB);
        conn.setConnectionType(EVcsConnectionType.PERSONAL_TOKEN);
        conn.setRefreshToken(null);
        conn.setTokenExpiresAt(LocalDateTime.now().plusHours(1));
        // No access token set → decryption will likely fail

        when(encryptionService.decrypt(any())).thenThrow(new RuntimeException("no token"));

        assertThatThrownBy(() -> provider.getClient(conn))
                .isInstanceOf(VcsClientException.class);
    }
}
