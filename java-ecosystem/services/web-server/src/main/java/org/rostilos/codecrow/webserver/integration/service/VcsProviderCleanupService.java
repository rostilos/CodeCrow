package org.rostilos.codecrow.webserver.integration.service;

import io.jsonwebtoken.Jwts;
import io.jsonwebtoken.security.Keys;
import okhttp3.OkHttpClient;
import okhttp3.Request;
import okhttp3.Response;
import org.rostilos.codecrow.core.model.vcs.BitbucketConnectInstallation;
import org.rostilos.codecrow.core.model.vcs.EVcsConnectionType;
import org.rostilos.codecrow.core.model.vcs.EVcsProvider;
import org.rostilos.codecrow.core.model.vcs.EVcsSetupStatus;
import org.rostilos.codecrow.core.model.vcs.VcsConnection;
import org.rostilos.codecrow.core.persistence.repository.vcs.BitbucketConnectInstallationRepository;
import org.rostilos.codecrow.core.persistence.repository.vcs.VcsConnectionRepository;
import org.rostilos.codecrow.core.service.SiteSettingsProvider;
import org.rostilos.codecrow.security.oauth.TokenEncryptionService;
import org.rostilos.codecrow.vcsclient.github.GitHubAppAuthService;
import org.rostilos.codecrow.vcsclient.gitlab.GitLabClientFactory;
import org.rostilos.codecrow.vcsclient.gitlab.GitLabOAuthProvider;
import org.rostilos.codecrow.webserver.exception.IntegrationException;
import org.slf4j.Logger;
import org.slf4j.LoggerFactory;
import org.springframework.beans.factory.annotation.Autowired;
import org.springframework.stereotype.Service;

import javax.crypto.SecretKey;
import java.io.IOException;
import java.nio.charset.StandardCharsets;
import java.security.MessageDigest;
import java.util.Date;
import java.util.HexFormat;
import java.util.Objects;

/**
 * Removes provider-side credentials or installations owned by an APP
 * connection before the local connection row is deleted.
 */
@Service
public class VcsProviderCleanupService {

    private static final Logger log = LoggerFactory.getLogger(VcsProviderCleanupService.class);

    private final SiteSettingsProvider siteSettingsProvider;
    private final TokenEncryptionService encryptionService;
    private final BitbucketConnectInstallationRepository connectInstallationRepository;
    private final VcsConnectionRepository connectionRepository;
    private final OkHttpClient httpClient;
    private final String bitbucketApiBaseUrl;

    @Autowired
    public VcsProviderCleanupService(
            SiteSettingsProvider siteSettingsProvider,
            TokenEncryptionService encryptionService,
            BitbucketConnectInstallationRepository connectInstallationRepository,
            VcsConnectionRepository connectionRepository) {
        this(
                siteSettingsProvider,
                encryptionService,
                connectInstallationRepository,
                connectionRepository,
                new OkHttpClient(),
                "https://api.bitbucket.org/2.0");
    }

    VcsProviderCleanupService(
            SiteSettingsProvider siteSettingsProvider,
            TokenEncryptionService encryptionService,
            BitbucketConnectInstallationRepository connectInstallationRepository,
            VcsConnectionRepository connectionRepository,
            OkHttpClient httpClient) {
        this(
                siteSettingsProvider,
                encryptionService,
                connectInstallationRepository,
                connectionRepository,
                httpClient,
                "https://api.bitbucket.org/2.0");
    }

    VcsProviderCleanupService(
            SiteSettingsProvider siteSettingsProvider,
            TokenEncryptionService encryptionService,
            BitbucketConnectInstallationRepository connectInstallationRepository,
            VcsConnectionRepository connectionRepository,
            OkHttpClient httpClient,
            String bitbucketApiBaseUrl) {
        this.siteSettingsProvider = siteSettingsProvider;
        this.encryptionService = encryptionService;
        this.connectInstallationRepository = connectInstallationRepository;
        this.connectionRepository = connectionRepository;
        this.httpClient = httpClient;
        this.bitbucketApiBaseUrl = bitbucketApiBaseUrl.replaceAll("/$", "");
    }

    /**
     * Remove the provider-side authorization represented by one connection.
     *
     * <p>Manual/PAT connections do not represent an App installation and are
     * intentionally left alone. A Bitbucket Connect installation is removed
     * with its installation-specific JWT. Bitbucket OAuth-consumer grants do
     * not expose an equivalent app-initiated revoke operation.</p>
     */
    public void removeProviderAuthorization(VcsConnection connection) {
        if (connection.getConnectionType() != EVcsConnectionType.APP) {
            return;
        }

        try {
            if (connection.getProviderType() == EVcsProvider.GITHUB) {
                deleteGitHubInstallation(connection);
            } else if (connection.getProviderType() == EVcsProvider.GITLAB) {
                revokeGitLabOAuthGrant(connection);
            } else if (connection.getProviderType() == EVcsProvider.BITBUCKET_CLOUD) {
                deleteBitbucketConnectInstallation(connection);
            }
        } catch (IntegrationException e) {
            throw e;
        } catch (Exception e) {
            throw new IntegrationException(
                    "Provider cleanup failed; the CodeCrow connection was kept so deletion can be retried: "
                            + e.getMessage());
        }
    }

    private void deleteGitHubInstallation(VcsConnection connection) throws Exception {
        String installationId = connection.getInstallationId();
        // Before installation_id existed, connected GitHub App rows stored the
        // installation ID in external_workspace_id. Pending request rows now
        // store a GitHub account ID there, so they must never use this fallback.
        boolean eligibleLegacyRow = connection.getSetupStatus() == EVcsSetupStatus.CONNECTED
                && connection.getGithubInstallationRequestId() == null
                && connection.getExternalWorkspaceSlug() != null
                && !connection.getExternalWorkspaceSlug().isBlank()
                && !Objects.equals(
                        connection.getExternalWorkspaceId(),
                        connection.getExternalWorkspaceSlug());
        if ((installationId == null || installationId.isBlank()) && eligibleLegacyRow) {
            installationId = connection.getExternalWorkspaceId();
        }
        if (installationId == null || !installationId.matches("\\d+")) {
            // OAuth fallback connections do not have a GitHub App installation.
            return;
        }

        boolean hasOtherLocalReference = connectionRepository
                .findAllByProviderTypeAndInstallationIdForUpdate(EVcsProvider.GITHUB, installationId)
                .stream()
                .anyMatch(candidate -> !Objects.equals(candidate.getId(), connection.getId())
                        && candidate.getConnectionType() == EVcsConnectionType.APP
                        && candidate.getSetupStatus() != EVcsSetupStatus.DISABLED);
        if (hasOtherLocalReference) {
            log.info("Keeping shared GitHub App installation {} while deleting connection {}; "
                            + "another CodeCrow connection still references it",
                    installationId, connection.getId());
            return;
        }

        createGitHubAppAuthService().deleteInstallation(Long.parseLong(installationId));
        log.info("Deleted GitHub App installation {} for connection {}",
                installationId, connection.getId());
    }

    private GitHubAppAuthService createGitHubAppAuthService() throws Exception {
        var settings = siteSettingsProvider.getGitHubSettings();
        if (settings.appId() == null || settings.appId().isBlank()) {
            throw new IntegrationException("GitHub App ID is not configured");
        }
        if (settings.privateKeyContent() != null && !settings.privateKeyContent().isBlank()) {
            return new GitHubAppAuthService(
                    settings.appId(),
                    GitHubAppAuthService.parsePrivateKeyContent(settings.privateKeyContent()));
        }
        if (settings.privateKeyPath() != null && !settings.privateKeyPath().isBlank()) {
            return new GitHubAppAuthService(settings.appId(), settings.privateKeyPath());
        }
        throw new IntegrationException("GitHub App private key is not configured");
    }

    private void revokeGitLabOAuthGrant(VcsConnection connection) throws Exception {
        if (connection.getAccessToken() == null || connection.getAccessToken().isBlank()) {
            return;
        }

        GitLabOAuthProvider oAuthProvider = GitLabOAuthProvider
                .from(siteSettingsProvider.getGitLabSettings())
                .requireConnectionIssuer(connection);
        String accessToken = encryptionService.decrypt(connection.getAccessToken());
        GitLabClientFactory.createOAuthClient(httpClient).revokeToken(
                oAuthProvider,
                accessToken);
        log.info("Revoked GitLab OAuth grant for connection {}", connection.getId());
    }

    private void deleteBitbucketConnectInstallation(VcsConnection connection) throws Exception {
        BitbucketConnectInstallation installation = connectInstallationRepository
                .findByVcsConnection_Id(connection.getId())
                .orElse(null);
        if (installation == null) {
            log.info("Bitbucket connection {} is not backed by a Connect installation; "
                            + "no app-initiated OAuth grant revoke is available",
                    connection.getId());
            return;
        }
        if (!installation.isEnabled()) {
            log.info("Bitbucket Connect installation {} is already disabled",
                    installation.getId());
            return;
        }
        if (installation.getClientKey() == null || installation.getClientKey().isBlank()
                || installation.getSharedSecret() == null
                || installation.getSharedSecret().isBlank()) {
            throw new IntegrationException(
                    "Bitbucket Connect installation credentials are incomplete");
        }

        String sharedSecret;
        try {
            sharedSecret = encryptionService.decrypt(installation.getSharedSecret());
        } catch (Exception e) {
            // Preserve compatibility with installation rows written before
            // shared-secret encryption was introduced.
            sharedSecret = installation.getSharedSecret();
        }

        String canonicalRequest = "DELETE&/2.0/addon&";
        String qsh = HexFormat.of().formatHex(
                MessageDigest.getInstance("SHA-256")
                        .digest(canonicalRequest.getBytes(StandardCharsets.UTF_8)));
        long now = System.currentTimeMillis();
        SecretKey key = Keys.hmacShaKeyFor(sharedSecret.getBytes(StandardCharsets.UTF_8));
        String jwt = Jwts.builder()
                .setIssuer(installation.getClientKey())
                .setSubject(installation.getClientKey())
                .setIssuedAt(new Date(now))
                .setExpiration(new Date(now + 180_000))
                .claim("qsh", qsh)
                .signWith(key)
                .compact();

        Request request = new Request.Builder()
                .url(bitbucketApiBaseUrl + "/addon")
                .header("Authorization", "JWT " + jwt)
                .delete()
                .build();
        try (Response response = httpClient.newCall(request).execute()) {
            if (!response.isSuccessful() && response.code() != 404) {
                throw new IOException(
                        "Bitbucket Connect uninstall returned " + response.code());
            }
        }

        installation.setEnabled(false);
        installation.setAccessToken(null);
        installation.setRefreshToken(null);
        installation.setTokenExpiresAt(null);
        connectInstallationRepository.save(installation);
        log.info("Deleted exact Bitbucket Connect installation {} for connection {}",
                installation.getId(), connection.getId());
    }
}
