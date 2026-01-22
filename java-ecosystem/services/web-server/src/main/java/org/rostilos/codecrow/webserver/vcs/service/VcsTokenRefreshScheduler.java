package org.rostilos.codecrow.webserver.vcs.service;

import org.rostilos.codecrow.core.model.vcs.EVcsConnectionType;
import org.rostilos.codecrow.core.model.vcs.EVcsProvider;
import org.rostilos.codecrow.core.model.vcs.EVcsSetupStatus;
import org.rostilos.codecrow.core.model.vcs.VcsConnection;
import org.rostilos.codecrow.core.persistence.repository.vcs.VcsConnectionRepository;
import org.rostilos.codecrow.vcsclient.VcsClientProvider;
import org.slf4j.Logger;
import org.slf4j.LoggerFactory;
import org.springframework.scheduling.annotation.Scheduled;
import org.springframework.stereotype.Service;

import java.time.LocalDateTime;
import java.util.List;

/**
 * Scheduled service to proactively refresh VCS OAuth tokens before they expire.
 * 
 * OAuth tokens (especially GitLab) expire quickly and refresh tokens expire after ~14 days
 * of inactivity. This scheduler keeps tokens fresh to prevent authentication failures.
 */
@Service
public class VcsTokenRefreshScheduler {
    
    private static final Logger log = LoggerFactory.getLogger(VcsTokenRefreshScheduler.class);
    
    // Refresh tokens that will expire in the next 3 days
    private static final int REFRESH_THRESHOLD_DAYS = 3;
    
    private final VcsConnectionRepository vcsConnectionRepository;
    private final VcsClientProvider vcsClientProvider;
    
    public VcsTokenRefreshScheduler(
            VcsConnectionRepository vcsConnectionRepository,
            VcsClientProvider vcsClientProvider
    ) {
        this.vcsConnectionRepository = vcsConnectionRepository;
        this.vcsClientProvider = vcsClientProvider;
    }
    
    /**
     * Runs every 6 hours to refresh tokens that are about to expire.
     * This prevents the "invalid_grant" error from expired refresh tokens.
     */
    @Scheduled(cron = "${vcs.token.refresh.cron:0 0 */6 * * *}")
    public void refreshExpiringTokens() {
        log.info("Starting scheduled VCS token refresh check");
        
        LocalDateTime thresholdTime = LocalDateTime.now().plusDays(REFRESH_THRESHOLD_DAYS);
        
        // Find all OAuth-based connections with tokens expiring soon
        List<VcsConnection> expiringConnections = vcsConnectionRepository.findAll().stream()
                .filter(conn -> conn.getSetupStatus() == EVcsSetupStatus.CONNECTED)
                .filter(conn -> isOAuthConnection(conn))
                .filter(conn -> conn.getTokenExpiresAt() != null)
                .filter(conn -> conn.getTokenExpiresAt().isBefore(thresholdTime))
                .toList();
        
        log.info("Found {} connections with tokens expiring before {}", 
                expiringConnections.size(), thresholdTime);
        
        int refreshed = 0;
        int failed = 0;
        
        for (VcsConnection connection : expiringConnections) {
            try {
                log.info("Proactively refreshing token for connection {} ({} - {})", 
                        connection.getId(), 
                        connection.getConnectionName(),
                        connection.getProviderType());
                
                // This will trigger token refresh via VcsClientProvider
                vcsClientProvider.getHttpClient(connection);
                
                refreshed++;
                log.info("Successfully refreshed token for connection {}", connection.getId());
                
            } catch (Exception e) {
                failed++;
                log.error("Failed to refresh token for connection {} ({}): {}", 
                        connection.getId(), 
                        connection.getConnectionName(),
                        e.getMessage());
                
                // Mark connection as having an error if refresh failed
                if (e.getMessage() != null && e.getMessage().contains("invalid_grant")) {
                    connection.setSetupStatus(EVcsSetupStatus.ERROR);
                    vcsConnectionRepository.save(connection);
                    log.warn("Connection {} marked as ERROR - user needs to re-authenticate", 
                            connection.getId());
                }
            }
        }
        
        log.info("VCS token refresh complete: {} refreshed, {} failed", refreshed, failed);
    }
    
    /**
     * Also refresh ALL OAuth connections once a week to keep refresh tokens alive,
     * even if access tokens haven't expired yet.
     * This prevents refresh token expiration due to inactivity.
     */
    @Scheduled(cron = "${vcs.token.keepalive.cron:0 0 3 * * SUN}")
    public void keepAliveAllTokens() {
        log.info("Starting weekly VCS token keep-alive refresh");
        
        List<VcsConnection> oauthConnections = vcsConnectionRepository.findAll().stream()
                .filter(conn -> conn.getSetupStatus() == EVcsSetupStatus.CONNECTED)
                .filter(conn -> isOAuthConnection(conn))
                .filter(conn -> conn.getRefreshToken() != null && !conn.getRefreshToken().isEmpty())
                .toList();
        
        log.info("Found {} OAuth connections to keep alive", oauthConnections.size());
        
        int refreshed = 0;
        int failed = 0;
        
        for (VcsConnection connection : oauthConnections) {
            try {
                // Force token refresh even if not expired
                vcsClientProvider.refreshToken(connection);
                refreshed++;
                log.debug("Keep-alive refresh successful for connection {}", connection.getId());
                
            } catch (Exception e) {
                failed++;
                log.warn("Keep-alive refresh failed for connection {}: {}", 
                        connection.getId(), e.getMessage());
            }
        }
        
        log.info("Weekly keep-alive complete: {} refreshed, {} failed", refreshed, failed);
    }
    
    private boolean isOAuthConnection(VcsConnection connection) {
        EVcsConnectionType type = connection.getConnectionType();
        return type == EVcsConnectionType.APP || 
               type == EVcsConnectionType.OAUTH_APP ||
               type == EVcsConnectionType.OAUTH_MANUAL;
    }
}
