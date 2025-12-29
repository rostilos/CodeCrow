package org.rostilos.codecrow.webserver.intergration.controller;

import org.rostilos.codecrow.webserver.generic.dto.message.MessageResponse;
import org.rostilos.codecrow.core.model.vcs.EVcsProvider;
import org.rostilos.codecrow.webserver.intergration.dto.response.VcsConnectionDTO;
import org.rostilos.codecrow.webserver.exception.IntegrationException;
import org.rostilos.codecrow.webserver.intergration.service.OAuthStateService;
import org.rostilos.codecrow.webserver.intergration.service.VcsIntegrationService;
import org.rostilos.codecrow.webserver.workspace.service.WorkspaceService;
import org.slf4j.Logger;
import org.slf4j.LoggerFactory;
import org.springframework.beans.factory.annotation.Value;
import org.springframework.http.HttpStatus;
import org.springframework.http.ResponseEntity;
import org.springframework.web.bind.annotation.*;

import java.io.IOException;
import java.net.URI;
import java.security.GeneralSecurityException;
import java.util.Map;

/**
 * Controller for OAuth callbacks that don't require workspace slug in the URL.
 * This is necessary because OAuth providers (like GitHub) only allow a single callback URL.
 * The workspace ID is encoded in the state parameter.
 */
@CrossOrigin(origins = "*", maxAge = 3600)
@RestController
@RequestMapping("/api/integrations")
public class OAuthCallbackController {
    
    private static final Logger log = LoggerFactory.getLogger(OAuthCallbackController.class);
    
    private final VcsIntegrationService integrationService;
    private final OAuthStateService oAuthStateService;
    private final WorkspaceService workspaceService;
    
    @Value("${codecrow.frontend-url:http://localhost:8080}")
    private String frontendUrl;
    
    public OAuthCallbackController(
            VcsIntegrationService integrationService,
            OAuthStateService oAuthStateService,
            WorkspaceService workspaceService
    ) {
        this.integrationService = integrationService;
        this.oAuthStateService = oAuthStateService;
        this.workspaceService = workspaceService;
    }

    @ExceptionHandler(IntegrationException.class)
    public ResponseEntity<Map<String, Object>> handleIntegrationException(IntegrationException e) {
        log.warn("Integration error: {}", e.getMessage());
        return ResponseEntity.badRequest().body(Map.of(
            "error", e.getErrorCode(),
            "message", e.getMessage()
        ));
    }
    
    /**
     * Handle OAuth callback from GitHub.
     * Supports both:
     * 1. OAuth App flow: receives 'code' parameter
     * 2. GitHub App installation flow: receives 'installation_id' parameter
     * 
     * The workspace ID is extracted from the state parameter.
     */
    @GetMapping("/github/app/callback")
    public ResponseEntity<?> handleGitHubCallback(
            @RequestParam(required = false) String code,
            @RequestParam(required = false) String state,
            @RequestParam(name = "installation_id", required = false) Long installationId,
            @RequestParam(name = "setup_action", required = false) String setupAction,
            @RequestParam(required = false) String error,
            @RequestParam(name = "error_description", required = false) String errorDescription
    ) {
        if (error != null) {
            log.warn("GitHub callback error: {} - {}", error, errorDescription);
            // Without workspace context, redirect to workspace selection
            String redirectUrl = frontendUrl + "/workspace?error=" + error;
            return ResponseEntity.status(HttpStatus.FOUND)
                    .location(URI.create(redirectUrl))
                    .build();
        }
        
        if (installationId != null) {
            log.info("GitHub App installation callback: installation_id={}, setup_action={}", 
                    installationId, setupAction);
            
            if (state == null) {
                log.warn("GitHub App installation without state parameter - installation_id={}", installationId);
                // Without workspace context, redirect to workspace selection
                String redirectUrl = frontendUrl + "/workspace?installation_id=" + installationId;
                return ResponseEntity.status(HttpStatus.FOUND)
                        .location(URI.create(redirectUrl))
                        .build();
            }
            
            try {
                Long workspaceId = extractWorkspaceIdFromState(state);
                
                if (workspaceId == null) {
                    log.error("Could not extract workspace ID from state: {}", state);
                    String redirectUrl = frontendUrl + "/workspace?error=invalid_state";
                    return ResponseEntity.status(HttpStatus.FOUND)
                            .location(URI.create(redirectUrl))
                            .build();
                }
                
                // Get workspace slug for the redirect URL
                String workspaceSlug = getWorkspaceSlug(workspaceId);
                
                VcsConnectionDTO connection = integrationService.handleGitHubAppInstallation(
                        installationId, workspaceId);
                
                String redirectUrl = frontendUrl + "/dashboard/" + workspaceSlug + "/projects/import?connectionId=" + connection.id() + "&provider=github&connectionType=APP";
                return ResponseEntity.status(HttpStatus.FOUND)
                        .location(URI.create(redirectUrl))
                        .build();
                        
            } catch (Exception e) {
                log.error("Failed to handle GitHub App installation callback", e);
                String redirectUrl = frontendUrl + "/workspace?error=installation_failed";
                return ResponseEntity.status(HttpStatus.FOUND)
                        .location(URI.create(redirectUrl))
                        .build();
            }
        }
        
        if (code == null) {
            return ResponseEntity.badRequest()
                    .body(new MessageResponse("Missing authorization code or installation_id"));
        }
        
        if (state == null) {
            return ResponseEntity.badRequest()
                    .body(new MessageResponse("Missing state parameter"));
        }
        
        try {
            // Extract workspace ID from state parameter
            Long workspaceId = extractWorkspaceIdFromState(state);
            
            if (workspaceId == null) {
                log.error("Could not extract workspace ID from state: {}", state);
                String redirectUrl = frontendUrl + "/workspace?error=invalid_state";
                return ResponseEntity.status(HttpStatus.FOUND)
                        .location(URI.create(redirectUrl))
                        .build();
            }
            
            // Get workspace slug for the redirect URL
            String workspaceSlug = getWorkspaceSlug(workspaceId);
            
            VcsConnectionDTO connection = integrationService.handleAppCallback(
                    EVcsProvider.GITHUB, code, state, workspaceId);
            
            // Redirect to frontend configure page for the new connection
            String redirectUrl = frontendUrl + "/dashboard/" + workspaceSlug + "/projects/import?connectionId=" + connection.id() + "&provider=github&connectionType=APP";
            return ResponseEntity.status(HttpStatus.FOUND)
                    .location(URI.create(redirectUrl))
                    .build();
            
        } catch (GeneralSecurityException | IOException e) {
            log.error("Failed to handle GitHub OAuth callback", e);
            String redirectUrl = frontendUrl + "/workspace?error=callback_failed";
            return ResponseEntity.status(HttpStatus.FOUND)
                    .location(URI.create(redirectUrl))
                    .build();
        } catch (Exception e) {
            log.error("Unexpected error during GitHub OAuth callback", e);
            String redirectUrl = frontendUrl + "/workspace?error=" + e.getMessage();
            return ResponseEntity.status(HttpStatus.FOUND)
                    .location(URI.create(redirectUrl))
                    .build();
        }
    }
    
    /**
     * Handle OAuth callback from Bitbucket Cloud.
     * The workspace ID is extracted from the state parameter.
     * 
     * GET /api/integrations/bitbucket-cloud/app/callback
     */
    @GetMapping("/bitbucket-cloud/app/callback")
    public ResponseEntity<?> handleBitbucketCallback(
            @RequestParam(required = false) String code,
            @RequestParam(required = false) String state,
            @RequestParam(required = false) String error,
            @RequestParam(name = "error_description", required = false) String errorDescription
    ) {
        if (error != null) {
            log.warn("Bitbucket OAuth callback error: {} - {}", error, errorDescription);
            String redirectUrl = frontendUrl + "/workspace?error=" + error;
            return ResponseEntity.status(HttpStatus.FOUND)
                    .location(URI.create(redirectUrl))
                    .build();
        }
        
        if (code == null) {
            return ResponseEntity.badRequest()
                    .body(new MessageResponse("Missing authorization code"));
        }
        
        if (state == null) {
            return ResponseEntity.badRequest()
                    .body(new MessageResponse("Missing state parameter"));
        }
        
        try {
            Long workspaceId = extractWorkspaceIdFromState(state);
            
            if (workspaceId == null) {
                log.error("Could not extract workspace ID from state: {}", state);
                String redirectUrl = frontendUrl + "/workspace?error=invalid_state";
                return ResponseEntity.status(HttpStatus.FOUND)
                        .location(URI.create(redirectUrl))
                        .build();
            }
            
            // Get workspace slug for the redirect URL
            String workspaceSlug = getWorkspaceSlug(workspaceId);
            
            VcsConnectionDTO connection = integrationService.handleAppCallback(
                    EVcsProvider.BITBUCKET_CLOUD, code, state, workspaceId);
            
            // Redirect to frontend configure page for the new connection
            String redirectUrl = frontendUrl + "/dashboard/" + workspaceSlug + "/projects/import?connectionId=" + connection.id() + "&provider=bitbucket-cloud&connectionType=APP";
            return ResponseEntity.status(HttpStatus.FOUND)
                    .location(URI.create(redirectUrl))
                    .build();
            
        } catch (GeneralSecurityException | IOException e) {
            log.error("Failed to handle Bitbucket OAuth callback", e);
            String redirectUrl = frontendUrl + "/workspace?error=callback_failed";
            return ResponseEntity.status(HttpStatus.FOUND)
                    .location(URI.create(redirectUrl))
                    .build();
        } catch (Exception e) {
            log.error("Unexpected error during Bitbucket OAuth callback", e);
            String redirectUrl = frontendUrl + "/workspace?error=" + e.getMessage();
            return ResponseEntity.status(HttpStatus.FOUND)
                    .location(URI.create(redirectUrl))
                    .build();
        }
    }

    private Long extractWorkspaceIdFromState(String state) {
        return oAuthStateService.validateAndExtractWorkspaceId(state);
    }
    
    private String getWorkspaceSlug(Long workspaceId) {
        try {
            return workspaceService.getWorkspaceById(workspaceId).getSlug();
        } catch (Exception e) {
            log.warn("Could not get workspace slug for ID {}: {}", workspaceId, e.getMessage());
            return "default";
        }
    }
}
