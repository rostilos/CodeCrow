package org.rostilos.codecrow.webserver.integration.controller;

import jakarta.validation.Valid;
import org.rostilos.codecrow.security.annotations.HasOwnerOrAdminRights;
import org.rostilos.codecrow.webserver.generic.dto.message.MessageResponse;
import org.rostilos.codecrow.core.model.vcs.EVcsConnectionType;
import org.rostilos.codecrow.core.model.vcs.EVcsProvider;
import org.rostilos.codecrow.webserver.integration.dto.request.RepoOnboardRequest;
import org.rostilos.codecrow.webserver.exception.IntegrationException;
import org.rostilos.codecrow.webserver.integration.dto.response.InstallUrlResponse;
import org.rostilos.codecrow.webserver.integration.dto.response.RepoOnboardResponse;
import org.rostilos.codecrow.webserver.integration.dto.response.VcsConnectionDTO;
import org.rostilos.codecrow.webserver.integration.dto.response.VcsRepositoryListDTO;
import org.rostilos.codecrow.webserver.integration.service.VcsIntegrationService;
import org.rostilos.codecrow.webserver.workspace.service.WorkspaceService;
import org.slf4j.Logger;
import org.slf4j.LoggerFactory;
import org.springframework.http.HttpStatus;
import org.springframework.http.ResponseEntity;
import org.springframework.web.bind.annotation.*;

import java.io.IOException;
import java.net.URI;
import java.security.GeneralSecurityException;
import java.util.List;
import java.util.Map;

/**
 * REST controller for VCS provider integrations.
 * Handles app installation, OAuth callbacks, repository listing, and onboarding.
 * 
 * All endpoints are provider-aware via the {provider} path variable.
 */
@CrossOrigin(origins = "*", maxAge = 3600)
@RestController
@RequestMapping("/api/{workspaceSlug}/integrations/{provider}")
public class VcsIntegrationController {
    
    private static final Logger log = LoggerFactory.getLogger(VcsIntegrationController.class);
    
    private final VcsIntegrationService integrationService;
    private final WorkspaceService workspaceService;
    
    public VcsIntegrationController(VcsIntegrationService integrationService, WorkspaceService workspaceService) {
        this.integrationService = integrationService;
        this.workspaceService = workspaceService;
    }
    
    /**
     * Handle IntegrationException and return a proper error response.
     */
    @ExceptionHandler(IntegrationException.class)
    public ResponseEntity<Map<String, Object>> handleIntegrationException(IntegrationException e) {
        log.warn("Integration error: {}", e.getMessage());
        return ResponseEntity.badRequest().body(Map.of(
            "error", e.getErrorCode(),
            "message", e.getMessage()
        ));
    }
    
    /**
     * Get the installation URL for a VCS provider app.
     * 
     * GET /api/{workspaceSlug}/integrations/{provider}/app/install-url
     */
    @GetMapping("/app/install-url")
    @HasOwnerOrAdminRights
    public ResponseEntity<InstallUrlResponse> getInstallUrl(
            @PathVariable String workspaceSlug,
            @PathVariable String provider
    ) {
        Long workspaceId = workspaceService.getWorkspaceBySlug(workspaceSlug).getId();
        EVcsProvider vcsProvider = parseProvider(provider);
        
        InstallUrlResponse response = integrationService.getInstallUrl(vcsProvider, workspaceId);
        return ResponseEntity.ok(response);
    }
    
    /**
     * Handle OAuth callback from a VCS provider app.
     * 
     * GET /api/{workspaceSlug}/integrations/{provider}/app/callback
     */
    @GetMapping("/app/callback")
    public ResponseEntity<?> handleAppCallback(
            @PathVariable String workspaceSlug,
            @PathVariable String provider,
            @RequestParam(required = false) String code,
            @RequestParam(required = false) String state,
            @RequestParam(required = false) String error,
            @RequestParam(name = "error_description", required = false) String errorDescription
    ) {
        if (error != null) {
            log.warn("OAuth callback error for {}: {} - {}", provider, error, errorDescription);
            // Redirect to frontend with error
            String redirectUrl = "/dashboard/hosting?error=" + error;
            return ResponseEntity.status(HttpStatus.FOUND)
                    .location(URI.create(redirectUrl))
                    .build();
        }
        
        if (code == null) {
            return ResponseEntity.badRequest()
                    .body(new MessageResponse("Missing authorization code"));
        }
        
        try {
            Long workspaceId = workspaceService.getWorkspaceBySlug(workspaceSlug).getId();
            EVcsProvider vcsProvider = parseProvider(provider);
            
            VcsConnectionDTO connection = integrationService.handleAppCallback(vcsProvider, code, state, workspaceId);
            
            // Redirect to frontend success page
            String redirectUrl = "/dashboard/hosting/" + provider + "/success?connectionId=" + connection.id();
            return ResponseEntity.status(HttpStatus.FOUND)
                    .location(URI.create(redirectUrl))
                    .build();
            
        } catch (GeneralSecurityException | IOException e) {
            log.error("Failed to handle OAuth callback", e);
            String redirectUrl = "/dashboard/hosting?error=callback_failed";
            return ResponseEntity.status(HttpStatus.FOUND)
                    .location(URI.create(redirectUrl))
                    .build();
        }
    }
    
    /**
     * List all VCS connections for a workspace.
     * 
     * GET /api/{workspaceSlug}/integrations/{provider}/connections
     * 
     * @param connectionType Optional filter: APP, OAUTH_MANUAL, PERSONAL_TOKEN, etc.
     */
    @GetMapping("/connections")
    @HasOwnerOrAdminRights
    public ResponseEntity<List<VcsConnectionDTO>> getConnections(
            @PathVariable String workspaceSlug,
            @PathVariable String provider,
            @RequestParam(required = false) String connectionType
    ) {
        Long workspaceId = workspaceService.getWorkspaceBySlug(workspaceSlug).getId();
        EVcsProvider vcsProvider = parseProvider(provider);
        
        EVcsConnectionType connType = null;
        if (connectionType != null && !connectionType.isBlank()) {
            try {
                connType = EVcsConnectionType.valueOf(connectionType.toUpperCase());
            } catch (IllegalArgumentException e) {
                log.warn("Invalid connection type: {}", connectionType);
            }
        }
        
        List<VcsConnectionDTO> connections = integrationService.getConnections(workspaceId, vcsProvider, connType);
        return ResponseEntity.ok(connections);
    }
    
    /**
     * Get a specific VCS connection.
     * 
     * GET /api/{workspaceSlug}/integrations/{provider}/connections/{connectionId}
     */
    @GetMapping("/connections/{connectionId}")
    @HasOwnerOrAdminRights
    public ResponseEntity<VcsConnectionDTO> getConnection(
            @PathVariable String workspaceSlug,
            @PathVariable String provider,
            @PathVariable Long connectionId
    ) {
        Long workspaceId = workspaceService.getWorkspaceBySlug(workspaceSlug).getId();
        parseProvider(provider); // Validate provider
        
        VcsConnectionDTO connection = integrationService.getConnectionDTO(workspaceId, connectionId);
        return ResponseEntity.ok(connection);
    }
    
    /**
     * Delete a VCS connection.
     * 
     * DELETE /api/{workspaceSlug}/integrations/{provider}/connections/{connectionId}
     */
    @DeleteMapping("/connections/{connectionId}")
    @HasOwnerOrAdminRights
    public ResponseEntity<Void> deleteConnection(
            @PathVariable String workspaceSlug,
            @PathVariable String provider,
            @PathVariable Long connectionId
    ) {
        Long workspaceId = workspaceService.getWorkspaceBySlug(workspaceSlug).getId();
        parseProvider(provider); // Validate provider
        
        integrationService.deleteConnection(workspaceId, connectionId);
        return ResponseEntity.noContent().build();
    }
    
    /**
     * Sync a VCS connection (refresh status and repo count).
     * 
     * POST /api/{workspaceSlug}/integrations/{provider}/connections/{connectionId}/sync
     */
    @PostMapping("/connections/{connectionId}/sync")
    @HasOwnerOrAdminRights
    public ResponseEntity<VcsConnectionDTO> syncConnection(
            @PathVariable String workspaceSlug,
            @PathVariable String provider,
            @PathVariable Long connectionId
    ) {
        Long workspaceId = workspaceService.getWorkspaceBySlug(workspaceSlug).getId();
        parseProvider(provider); // Validate provider
        
        VcsConnectionDTO connection = integrationService.syncConnection(workspaceId, connectionId);
        return ResponseEntity.ok(connection);
    }
    
    /**
     * Get reconnect URL for a VCS connection.
     * Used to re-authorize connections with expired or invalid tokens.
     * 
     * GET /api/{workspaceSlug}/integrations/{provider}/connections/{connectionId}/reconnect-url
     */
    @GetMapping("/connections/{connectionId}/reconnect-url")
    @HasOwnerOrAdminRights
    public ResponseEntity<InstallUrlResponse> getReconnectUrl(
            @PathVariable String workspaceSlug,
            @PathVariable String provider,
            @PathVariable Long connectionId
    ) {
        Long workspaceId = workspaceService.getWorkspaceBySlug(workspaceSlug).getId();
        parseProvider(provider); // Validate provider
        
        InstallUrlResponse response = integrationService.getReconnectUrl(workspaceId, connectionId);
        return ResponseEntity.ok(response);
    }
    
    /**
     * Refresh the token for a GitHub App connection directly (server-side).
     * Unlike reconnect-url which redirects to the provider, this endpoint
     * refreshes the installation token using the App's private key without
     * requiring user interaction.
     * 
     * POST /api/{workspaceSlug}/integrations/{provider}/connections/{connectionId}/refresh-token
     */
    @PostMapping("/connections/{connectionId}/refresh-token")
    @HasOwnerOrAdminRights
    public ResponseEntity<VcsConnectionDTO> refreshConnectionToken(
            @PathVariable String workspaceSlug,
            @PathVariable String provider,
            @PathVariable Long connectionId
    ) {
        Long workspaceId = workspaceService.getWorkspaceBySlug(workspaceSlug).getId();
        parseProvider(provider); // Validate provider
        
        VcsConnectionDTO connection = integrationService.refreshConnectionToken(workspaceId, connectionId);
        return ResponseEntity.ok(connection);
    }
    
    /**
     * List repositories from a VCS connection.
     * 
     * GET /api/{workspaceSlug}/integrations/{provider}/repos
     */
    @GetMapping("/repos")
    @HasOwnerOrAdminRights
    public ResponseEntity<VcsRepositoryListDTO> listRepositories(
            @PathVariable String workspaceSlug,
            @PathVariable String provider,
            @RequestParam Long vcsConnectionId,
            @RequestParam(required = false) String q,
            @RequestParam(defaultValue = "1") int page
    ) {
        try {
            Long workspaceId = workspaceService.getWorkspaceBySlug(workspaceSlug).getId();
            parseProvider(provider); // Validate provider
            
            VcsRepositoryListDTO repos = integrationService.listRepositories(workspaceId, vcsConnectionId, q, page);
            return ResponseEntity.ok(repos);
            
        } catch (IOException e) {
            log.error("Failed to list repositories", e);
            throw new IntegrationException("Failed to list repositories: " + e.getMessage());
        }
    }
    
    /**
     * List branches in a repository.
     * Supports optional search filter and limit for performance with large repos.
     * 
     * GET /api/{workspaceSlug}/integrations/{provider}/repos/{externalRepoId}/branches
     * 
     * @param search Optional search query to filter branch names (partial match)
     * @param limit Optional maximum number of results (default: 100, 0 for unlimited)
     */
    @GetMapping("/repos/{externalRepoId}/branches")
    @HasOwnerOrAdminRights
    public ResponseEntity<List<String>> listBranches(
            @PathVariable String workspaceSlug,
            @PathVariable String provider,
            @PathVariable String externalRepoId,
            @RequestParam Long vcsConnectionId,
            @RequestParam(required = false) String search,
            @RequestParam(required = false, defaultValue = "100") int limit
    ) {
        try {
            Long workspaceId = workspaceService.getWorkspaceBySlug(workspaceSlug).getId();
            parseProvider(provider); // Validate provider
            
            List<String> branches = integrationService.listBranches(
                workspaceId, vcsConnectionId, externalRepoId, search, limit
            );
            return ResponseEntity.ok(branches);
            
        } catch (IOException e) {
            log.error("Failed to list branches", e);
            throw new IntegrationException("Failed to list branches: " + e.getMessage());
        }
    }
    
    /**
     * Get a specific repository from a VCS connection.
     * 
     * GET /api/{workspaceSlug}/integrations/{provider}/repos/{externalRepoId}
     */
    @GetMapping("/repos/{externalRepoId}")
    @HasOwnerOrAdminRights
    public ResponseEntity<VcsRepositoryListDTO.VcsRepositoryDTO> getRepository(
            @PathVariable String workspaceSlug,
            @PathVariable String provider,
            @PathVariable String externalRepoId,
            @RequestParam Long vcsConnectionId
    ) {
        try {
            Long workspaceId = workspaceService.getWorkspaceBySlug(workspaceSlug).getId();
            parseProvider(provider); // Validate provider
            
            VcsRepositoryListDTO.VcsRepositoryDTO repo = integrationService.getRepository(
                    workspaceId, vcsConnectionId, externalRepoId);
            return ResponseEntity.ok(repo);
            
        } catch (IOException e) {
            log.error("Failed to get repository", e);
            throw new IntegrationException("Failed to get repository: " + e.getMessage());
        }
    }
    
    /**
     * Onboard a repository (create project + binding + webhooks).
     * 
     * POST /api/{workspaceSlug}/integrations/{provider}/repos/{externalRepoId}/onboard
     */
    @PostMapping("/repos/{externalRepoId}/onboard")
    @HasOwnerOrAdminRights
    public ResponseEntity<RepoOnboardResponse> onboardRepository(
            @PathVariable String workspaceSlug,
            @PathVariable String provider,
            @PathVariable String externalRepoId,
            @Valid @RequestBody RepoOnboardRequest request
    ) {
        try {
            Long workspaceId = workspaceService.getWorkspaceBySlug(workspaceSlug).getId();
            EVcsProvider vcsProvider = parseProvider(provider);
            
            RepoOnboardResponse response = integrationService.onboardRepository(
                    workspaceId, vcsProvider, externalRepoId, request);
            return ResponseEntity.status(HttpStatus.CREATED).body(response);
            
        } catch (IOException e) {
            log.error("Failed to onboard repository", e);
            throw new IntegrationException("Failed to onboard repository: " + e.getMessage());
        }
    }
    
    // ========== Helper Methods ==========
    
    private EVcsProvider parseProvider(String provider) {
        try {
            return EVcsProvider.fromId(provider);
        } catch (IllegalArgumentException e) {
            throw new IntegrationException("Unknown VCS provider: " + provider);
        }
    }
}
