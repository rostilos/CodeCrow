package org.rostilos.codecrow.webserver.integration.controller;

import org.rostilos.codecrow.webserver.generic.dto.message.MessageResponse;
import org.rostilos.codecrow.core.model.vcs.EVcsProvider;
import org.rostilos.codecrow.webserver.integration.dto.response.VcsConnectionDTO;
import org.rostilos.codecrow.webserver.integration.service.VcsIntegrationService;
import org.slf4j.Logger;
import org.slf4j.LoggerFactory;
import org.springframework.beans.factory.annotation.Value;
import org.springframework.http.HttpStatus;
import org.springframework.http.ResponseEntity;
import org.springframework.web.bind.annotation.*;

import java.io.IOException;
import java.net.URI;
import java.nio.charset.StandardCharsets;
import java.security.GeneralSecurityException;
import java.util.Base64;

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

    @Value("${codecrow.frontend-url:http://localhost:8080}")
    private String frontendUrl;
    
    public VcsIntegrationCallbackController(VcsIntegrationService integrationService) {
        this.integrationService = integrationService;
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
            String redirectUrl = frontendUrl + "/dashboard/hosting?error=" + error;
            return ResponseEntity.status(HttpStatus.FOUND)
                    .location(URI.create(redirectUrl))
                    .build();
        }
        
        if (code == null || state == null) {
            return ResponseEntity.badRequest()
                    .body(new MessageResponse("Missing authorization code or state"));
        }
        
        try {
            // Decode state to get workspace ID
            StateData stateData = decodeState(state);
            EVcsProvider vcsProvider = EVcsProvider.fromId(provider);
            
            if (stateData.provider() != vcsProvider) {
                throw new IllegalArgumentException("State provider mismatch");
            }
            
            VcsConnectionDTO connection = integrationService.handleAppCallback(
                    vcsProvider, code, state, stateData.workspaceId());
            
            // Redirect to frontend success page
            String redirectUrl = frontendUrl + "/dashboard/hosting/" + provider + 
                    "/success?connectionId=" + connection.id();
            return ResponseEntity.status(HttpStatus.FOUND)
                    .location(URI.create(redirectUrl))
                    .build();
            
        } catch (GeneralSecurityException | IOException e) {
            log.error("Failed to handle OAuth callback", e);
            String redirectUrl = frontendUrl + "/dashboard/hosting?error=callback_failed";
            return ResponseEntity.status(HttpStatus.FOUND)
                    .location(URI.create(redirectUrl))
                    .build();
        } catch (IllegalArgumentException e) {
            log.error("Invalid state parameter", e);
            String redirectUrl = frontendUrl + "/dashboard/hosting?error=invalid_state";
            return ResponseEntity.status(HttpStatus.FOUND)
                    .location(URI.create(redirectUrl))
                    .build();
        }
    }
    
    private StateData decodeState(String state) {
        try {
            String decoded = new String(Base64.getDecoder().decode(state), StandardCharsets.UTF_8);
            String[] parts = decoded.split(":");
            if (parts.length < 2) {
                throw new IllegalArgumentException("Invalid state format");
            }
            EVcsProvider provider = EVcsProvider.fromId(parts[0]);
            Long workspaceId = Long.parseLong(parts[1]);
            return new StateData(provider, workspaceId);
        } catch (Exception e) {
            throw new IllegalArgumentException("Failed to decode state: " + e.getMessage());
        }
    }
    
    private record StateData(EVcsProvider provider, Long workspaceId) {}
}
