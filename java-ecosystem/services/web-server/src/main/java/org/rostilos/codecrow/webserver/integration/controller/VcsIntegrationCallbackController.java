package org.rostilos.codecrow.webserver.integration.controller;

import org.rostilos.codecrow.webserver.generic.dto.message.MessageResponse;
import org.rostilos.codecrow.core.model.vcs.EVcsProvider;
import org.rostilos.codecrow.webserver.exception.GitHubInstallationRecoveryException;
import org.rostilos.codecrow.webserver.integration.dto.response.VcsConnectionDTO;
import org.rostilos.codecrow.webserver.integration.service.OAuthStateService;
import org.rostilos.codecrow.webserver.integration.service.VcsIntegrationService;
import org.rostilos.codecrow.core.service.SiteSettingsProvider;
import org.slf4j.Logger;
import org.slf4j.LoggerFactory;
import org.springframework.http.HttpStatus;
import org.springframework.http.ResponseEntity;
import org.springframework.web.bind.annotation.*;

import java.io.IOException;
import java.net.URI;
import java.net.URLEncoder;
import java.nio.charset.StandardCharsets;
import java.security.GeneralSecurityException;

/**
 * Public OAuth callback endpoint for VCS provider apps.
 * This handles the OAuth redirect from VCS providers (which don't know the workspace).
 * The workspace ID is encoded in the state parameter.
 */
@CrossOrigin(origins = "*", maxAge = 3600)
@RestController
@RequestMapping("/api/integrations/{provider}/app")
public class VcsIntegrationCallbackController {
    
    private static final Logger log = LoggerFactory.getLogger(VcsIntegrationCallbackController.class);
    
    private final VcsIntegrationService integrationService;
    private final SiteSettingsProvider siteSettingsProvider;
    private final OAuthStateService oAuthStateService;
    
    public VcsIntegrationCallbackController(VcsIntegrationService integrationService,
                                            SiteSettingsProvider siteSettingsProvider,
                                            OAuthStateService oAuthStateService) {
        this.integrationService = integrationService;
        this.siteSettingsProvider = siteSettingsProvider;
        this.oAuthStateService = oAuthStateService;
    }
    
    private String getFrontendUrl() {
        return siteSettingsProvider.getBaseUrlSettings().frontendUrl();
    }
    
    /**
     * Handle OAuth callback from a VCS provider app.
     * The workspace ID is extracted from the state parameter.
     * 
     * GET /api/integrations/{provider}/app/callback
     */
    @GetMapping("/callback")
    public ResponseEntity<?> handleAppCallback(
            @PathVariable String provider,
            @RequestParam(required = false) String code,
            @RequestParam(required = false) String state,
            @RequestParam(required = false) String error,
            @RequestParam(name = "error_description", required = false) String errorDescription
    ) {
        if (error != null) {
            log.warn("OAuth callback error for {}: {} - {}", provider, error, errorDescription);
            String redirectUrl = publicResultUrl(provider, "error", null, error);
            return ResponseEntity.status(HttpStatus.FOUND)
                    .location(URI.create(redirectUrl))
                    .build();
        }
        
        if (code == null || state == null) {
            return ResponseEntity.badRequest()
                    .body(new MessageResponse("Missing authorization code or state"));
        }
        
        try {
            EVcsProvider vcsProvider = EVcsProvider.fromId(provider);
            OAuthStateService.OAuthStateData stateData = oAuthStateService.validateAndExtractState(state);
            if (stateData == null) {
                throw new IllegalArgumentException("Invalid state");
            }
            
            if (!vcsProvider.getId().equals(stateData.providerId())) {
                throw new IllegalArgumentException("State provider mismatch");
            }
            
            VcsConnectionDTO connection = integrationService.handleAppCallback(
                    vcsProvider, code, state, stateData.workspaceId());
            
            // Redirect to frontend success page
            String redirectUrl = publicResultUrl(provider, "connected", connection.id(), null);
            return ResponseEntity.status(HttpStatus.FOUND)
                    .location(URI.create(redirectUrl))
                    .build();
            
        } catch (GitHubInstallationRecoveryException e) {
            return ResponseEntity.status(HttpStatus.FOUND)
                    .location(URI.create(e.getRedirectUrl()))
                    .build();
        } catch (GeneralSecurityException | IOException e) {
            log.error("Failed to handle OAuth callback", e);
            String redirectUrl = publicResultUrl(provider, "error", null, "callback_failed");
            return ResponseEntity.status(HttpStatus.FOUND)
                    .location(URI.create(redirectUrl))
                    .build();
        } catch (IllegalArgumentException e) {
            log.error("Invalid state parameter", e);
            String redirectUrl = publicResultUrl(provider, "error", null, "invalid_state");
            return ResponseEntity.status(HttpStatus.FOUND)
                    .location(URI.create(redirectUrl))
                    .build();
        }
    }

    private String publicResultUrl(
            String provider,
            String status,
            Long connectionId,
            String errorCode) {
        StringBuilder url = new StringBuilder(getFrontendUrl())
                .append("/integrations/app-installed?provider=")
                .append(URLEncoder.encode(provider, StandardCharsets.UTF_8))
                .append("&status=")
                .append(URLEncoder.encode(status, StandardCharsets.UTF_8));
        if (connectionId != null) {
            url.append("&connectionId=").append(connectionId);
        }
        if (errorCode != null && !errorCode.isBlank()) {
            url.append("&error=")
                    .append(URLEncoder.encode(errorCode, StandardCharsets.UTF_8));
        }
        return url.toString();
    }
    
}
