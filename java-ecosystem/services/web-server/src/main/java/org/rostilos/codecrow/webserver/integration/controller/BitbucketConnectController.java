package org.rostilos.codecrow.webserver.integration.controller;

import com.fasterxml.jackson.databind.JsonNode;
import com.fasterxml.jackson.databind.ObjectMapper;
import io.jsonwebtoken.Claims;
import org.rostilos.codecrow.core.model.vcs.BitbucketConnectInstallation;
import org.rostilos.codecrow.security.annotations.HasOwnerOrAdminRights;
import org.rostilos.codecrow.security.service.UserDetailsImpl;
import org.rostilos.codecrow.webserver.integration.dto.response.VcsConnectionDTO;
import org.rostilos.codecrow.webserver.exception.IntegrationException;
import org.rostilos.codecrow.webserver.integration.service.BitbucketConnectService;
import org.rostilos.codecrow.webserver.admin.service.ISiteSettingsProvider;
import org.slf4j.Logger;
import org.slf4j.LoggerFactory;
import org.springframework.http.HttpStatus;
import org.springframework.http.ResponseEntity;
import org.springframework.security.access.prepost.PreAuthorize;
import org.springframework.security.core.annotation.AuthenticationPrincipal;
import org.springframework.web.bind.annotation.*;

import java.io.IOException;
import java.net.URLEncoder;
import java.nio.charset.StandardCharsets;
import java.security.GeneralSecurityException;
import java.util.HashMap;
import java.util.List;
import java.util.Map;
import java.util.UUID;
import java.util.concurrent.ConcurrentHashMap;

/**
 * Controller for Bitbucket Connect App lifecycle events and management.
 * 
 * Handles:
 * - Connect App descriptor delivery
 * - Lifecycle callbacks (installed, uninstalled, enabled, disabled)
 * - Installation management (linking to CodeCrow workspace)
 * - 1-click install flow with state tracking
 */
@RestController
@RequestMapping("/api/bitbucket/connect")
public class BitbucketConnectController {
    
    private static final Logger log = LoggerFactory.getLogger(BitbucketConnectController.class);
    
    // Store pending installation requests: state -> workspaceId
    // In production, use Redis or database for distributed systems
    private static final Map<String, PendingInstall> pendingInstalls = new ConcurrentHashMap<>();
    
    private final BitbucketConnectService connectService;
    private final ObjectMapper objectMapper;
    private final ISiteSettingsProvider siteSettingsProvider;
    
    public BitbucketConnectController(BitbucketConnectService connectService,
                                      ISiteSettingsProvider siteSettingsProvider) {
        this.connectService = connectService;
        this.objectMapper = new ObjectMapper();
        this.siteSettingsProvider = siteSettingsProvider;
    }
    
    private String getFrontendUrl() {
        return siteSettingsProvider.getBaseUrlSettings().frontendUrl();
    }
    
    // ==================== 1-Click Install Flow ====================
    
    /**
     * Start the 1-click installation flow.
     * Generates a state token and returns the Bitbucket install URL.
     * The frontend will redirect the user to this URL.
     */
    @PostMapping("/install/start")
    @HasOwnerOrAdminRights
    public ResponseEntity<Map<String, String>> startInstall(
            @RequestParam Long workspaceId,
            @RequestParam(required = false) String workspaceSlug) {
        
        // Generate unique state token
        String state = UUID.randomUUID().toString();
        
        // Store pending install info (expires in 10 minutes)
        pendingInstalls.put(state, new PendingInstall(workspaceId, workspaceSlug, System.currentTimeMillis()));
        
        // Clean up old pending installs
        cleanupOldPendingInstalls();
        
        // Build install URL with state
        String installUrl = String.format(
            "https://bitbucket.org/site/addons/authorize?addon_key=codecrow-connect-app&state=%s",
            URLEncoder.encode(state, StandardCharsets.UTF_8)
        );
        
        Map<String, String> response = new HashMap<>();
        response.put("installUrl", installUrl);
        response.put("state", state);
        
        log.info("Started Connect App install flow for workspace {} with state {}", workspaceId, state);
        
        return ResponseEntity.ok(response);
    }
    
    /**
     * Check the status of a pending installation.
     * Frontend polls this after redirecting user to Bitbucket.
     */
    @GetMapping("/install/status")
    @HasOwnerOrAdminRights
    public ResponseEntity<Map<String, Object>> checkInstallStatus(@RequestParam String state) {
        PendingInstall pending = pendingInstalls.get(state);
        
        Map<String, Object> response = new HashMap<>();
        
        if (pending == null) {
            response.put("status", "not_found");
            return ResponseEntity.ok(response);
        }
        
        if (pending.completed) {
            response.put("status", "completed");
            response.put("installationId", pending.installationId);
            response.put("connectionId", pending.connectionId);
            response.put("workspaceSlug", pending.bitbucketWorkspaceSlug);
            
            // Clean up
            pendingInstalls.remove(state);
            
            return ResponseEntity.ok(response);
        }
        
        // Check if expired (10 minutes)
        if (System.currentTimeMillis() - pending.createdAt > 600_000) {
            pendingInstalls.remove(state);
            response.put("status", "expired");
            return ResponseEntity.ok(response);
        }
        
        response.put("status", "pending");
        return ResponseEntity.ok(response);
    }
    
    // ==================== Connect App Descriptor ====================
    
    /**
     * Serve the Connect App descriptor (atlassian-connect.json).
     * This endpoint is called by Bitbucket when registering the app.
     */
    @GetMapping(value = "/descriptor", produces = "application/json")
    public ResponseEntity<JsonNode> getDescriptor() {
        //TODO: simplified setup ( on-premises/opensource simple setup )
        // if (!connectService.isConfigured()) {
        //     log.warn("Bitbucket Connect App not configured");
        //     return ResponseEntity.status(HttpStatus.SERVICE_UNAVAILABLE).build();
        // }
        
        JsonNode descriptor = connectService.getDescriptor();
        return ResponseEntity.ok(descriptor);
    }
    
    // ==================== Lifecycle Callbacks ====================
    
    /**
     * Handle the "installed" lifecycle event.
     * Called by Bitbucket when a workspace admin installs the Connect App.
     */
    @PostMapping(value = "/installed", consumes = "application/json")
    public ResponseEntity<Void> handleInstalled(
            @RequestBody String payloadStr,
            @RequestParam(required = false) String state) {
        
        log.info("Received Connect App installation callback, state: {}", state);
        
        try {
            JsonNode payload = objectMapper.readTree(payloadStr);
            BitbucketConnectInstallation installation = connectService.handleInstalled(payload);
            
            log.info("Connect App installed successfully for workspace: {}", 
                    installation.getBitbucketWorkspaceSlug());
            
            // Try to find and complete a pending install
            // Bitbucket doesn't pass state back, so we match by finding any recent pending install
            PendingInstall matchedPending = null;
            String matchedState = null;
            
            // First try exact state match
            if (state != null && pendingInstalls.containsKey(state)) {
                matchedPending = pendingInstalls.get(state);
                matchedState = state;
            } else {
                // Find the most recent pending install (within last 5 minutes)
                long now = System.currentTimeMillis();
                for (Map.Entry<String, PendingInstall> entry : pendingInstalls.entrySet()) {
                    PendingInstall pending = entry.getValue();
                    if (!pending.completed && (now - pending.createdAt) < 300_000) {
                        if (matchedPending == null || pending.createdAt > matchedPending.createdAt) {
                            matchedPending = pending;
                            matchedState = entry.getKey();
                        }
                    }
                }
            }
            
            if (matchedPending != null) {
                log.info("Found pending install for state: {}, workspace: {}", 
                        matchedState, matchedPending.workspaceId);
                
                try {
                    // Auto-link the installation to the CodeCrow workspace
                    VcsConnectionDTO connection = connectService.linkToCodecrowWorkspace(
                            installation.getId(), 
                            matchedPending.workspaceId
                    );
                    
                    // Mark as completed
                    matchedPending.completed = true;
                    matchedPending.installationId = installation.getId();
                    matchedPending.connectionId = connection.id();
                    matchedPending.bitbucketWorkspaceSlug = installation.getBitbucketWorkspaceSlug();
                    
                    log.info("Auto-linked installation {} to workspace {}", 
                            installation.getId(), matchedPending.workspaceId);
                    
                } catch (Exception e) {
                    log.warn("Failed to auto-link installation: {}", e.getMessage(), e);
                    // Still mark as completed but without connection
                    matchedPending.completed = true;
                    matchedPending.installationId = installation.getId();
                    matchedPending.bitbucketWorkspaceSlug = installation.getBitbucketWorkspaceSlug();
                }
            } else {
                log.info("No pending install found, installation saved as unlinked");
            }
            
            return ResponseEntity.ok().build();
            
        } catch (Exception e) {
            log.error("Failed to process installation callback", e);
            return ResponseEntity.status(HttpStatus.INTERNAL_SERVER_ERROR).build();
        }
    }
    
    /**
     * Handle the "uninstalled" lifecycle event.
     * Called when the app is uninstalled from a workspace.
     */
    @PostMapping("/uninstalled")
    public ResponseEntity<Void> handleUninstalled(
            @RequestHeader(value = "Authorization", required = false) String authHeader,
            @RequestBody(required = false) String payloadStr) {
        
        log.info("Received Connect App uninstallation callback");
        
        try {
            String clientKey = null;
            
            // Try to get clientKey from JWT first
            if (authHeader != null && authHeader.startsWith("JWT ")) {
                String jwt = authHeader.substring(4);
                Claims claims = connectService.verifyJwt(jwt);
                if (claims != null) {
                    clientKey = claims.getIssuer();
                }
            }
            
            // If no JWT, try to get from payload
            if (clientKey == null && payloadStr != null) {
                JsonNode payload = objectMapper.readTree(payloadStr);
                clientKey = payload.path("clientKey").asText(null);
            }
            
            if (clientKey == null) {
                log.warn("Could not determine clientKey for uninstallation");
                return ResponseEntity.badRequest().build();
            }
            
            connectService.handleUninstalled(clientKey);
            return ResponseEntity.ok().build();
            
        } catch (Exception e) {
            log.error("Failed to process uninstallation callback", e);
            return ResponseEntity.status(HttpStatus.INTERNAL_SERVER_ERROR).build();
        }
    }
    
    /**
     * Handle the "enabled" lifecycle event.
     */
    @PostMapping("/enabled")
    public ResponseEntity<Void> handleEnabled(
            @RequestHeader(value = "Authorization", required = false) String authHeader) {
        
        log.info("Received Connect App enabled callback");
        
        try {
            String clientKey = extractClientKeyFromJwt(authHeader);
            if (clientKey == null) {
                return ResponseEntity.status(HttpStatus.UNAUTHORIZED).build();
            }
            
            connectService.handleEnabled(clientKey);
            return ResponseEntity.ok().build();
            
        } catch (Exception e) {
            log.error("Failed to process enabled callback", e);
            return ResponseEntity.status(HttpStatus.INTERNAL_SERVER_ERROR).build();
        }
    }
    
    /**
     * Handle the "disabled" lifecycle event.
     */
    @PostMapping("/disabled")
    public ResponseEntity<Void> handleDisabled(
            @RequestHeader(value = "Authorization", required = false) String authHeader) {
        
        log.info("Received Connect App disabled callback");
        
        try {
            String clientKey = extractClientKeyFromJwt(authHeader);
            if (clientKey == null) {
                return ResponseEntity.status(HttpStatus.UNAUTHORIZED).build();
            }
            
            connectService.handleDisabled(clientKey);
            return ResponseEntity.ok().build();
            
        } catch (Exception e) {
            log.error("Failed to process disabled callback", e);
            return ResponseEntity.status(HttpStatus.INTERNAL_SERVER_ERROR).build();
        }
    }
    
    // ==================== Configure Page (Post-Install Handshake) ====================
    
    /**
     * Serve the Configure Page for linking the installation to a CodeCrow workspace.
     * 
     * This is called when user clicks "Configure" or "Get Started" in Bitbucket after installation.
     * Bitbucket passes a signed_request (JWT) that identifies the installation.
     * The page redirects to CodeCrow frontend with the clientKey for the handshake.
     */
    @GetMapping(value = "/configure", produces = "text/html")
    public ResponseEntity<String> getConfigurePage(
            @RequestParam(value = "signed_request", required = false) String signedRequest,
            @RequestParam(value = "jwt", required = false) String jwtParam) {
        
        log.info("Configure page requested, signed_request: {}, jwt present: {}", 
                signedRequest, jwtParam != null);
        
        // Prefer jwt parameter, fall back to signed_request if it's not a placeholder
        String jwt = jwtParam;
        if ((jwt == null || jwt.isBlank()) && signedRequest != null && !signedRequest.startsWith("{")) {
            jwt = signedRequest;
        }
        
        if (jwt == null || jwt.isBlank()) {
            return ResponseEntity.badRequest().body(generateErrorPage("Missing authentication token"));
        }
        
        try {
            // Verify the JWT and extract clientKey
            Claims claims = connectService.verifyJwt(jwt);
            if (claims == null) {
                return ResponseEntity.status(HttpStatus.UNAUTHORIZED)
                        .body(generateErrorPage("Invalid or expired token"));
            }
            
            String clientKey = claims.getIssuer();
            log.info("Configure page for clientKey: {}", clientKey);
            
            // Find the installation
            var installationOpt = connectService.findByClientKey(clientKey);
            if (installationOpt.isEmpty()) {
                return ResponseEntity.status(HttpStatus.NOT_FOUND)
                        .body(generateErrorPage("Installation not found"));
            }
            
            var installation = installationOpt.get();
            
            // If already linked, show success
            if (installation.getCodecrowWorkspace() != null) {
                return buildFrameableResponse(generateAlreadyLinkedPage(installation));
            }
            
            // Generate the handshake page that redirects to CodeCrow frontend
            return buildFrameableResponse(generateHandshakePage(clientKey, installation));
            
        } catch (Exception e) {
            log.error("Error processing configure page", e);
            return buildFrameableResponse(generateErrorPage("An error occurred: " + e.getMessage()));
        }
    }
    
    /**
     * Build a response that can be displayed in Bitbucket's iframe.
     * Sets proper headers to allow framing from Bitbucket and prevent caching.
     * Note: X-Frame-Options ALLOW-FROM is deprecated, using CSP frame-ancestors instead.
     */
    private ResponseEntity<String> buildFrameableResponse(String htmlContent) {
        return ResponseEntity.ok()
                .header("Content-Type", "text/html; charset=UTF-8")
                // CSP frame-ancestors is the modern replacement for X-Frame-Options
                .header("Content-Security-Policy", "frame-ancestors 'self' https://bitbucket.org https://*.bitbucket.org https://*.atlassian.net")
                // Prevent caching to avoid stale content
                .header("Cache-Control", "no-store, no-cache, must-revalidate, max-age=0")
                .header("Pragma", "no-cache")
                .header("Expires", "0")
                // Explicitly remove X-Frame-Options (CSP takes precedence)
                .header("X-Frame-Options", "")
                .body(htmlContent);
    }
    
    /**
     * Complete the handshake - link installation to CodeCrow workspace.
     * Called from the frontend after user selects a workspace.
     */
    @PostMapping("/configure/link")
    @PreAuthorize("isAuthenticated()")
    public ResponseEntity<Map<String, Object>> linkFromConfigure(
            @RequestParam String clientKey,
            @RequestParam Long workspaceId,
            @AuthenticationPrincipal UserDetailsImpl userDetails) {
        
        log.info("Linking installation {} to workspace {} by user {}", 
                clientKey, workspaceId, userDetails.getUsername());
        
        try {
            var installationOpt = connectService.findByClientKey(clientKey);
            if (installationOpt.isEmpty()) {
                return ResponseEntity.status(HttpStatus.NOT_FOUND)
                        .body(Map.of("error", "Installation not found"));
            }
            
            var installation = installationOpt.get();
            
            // Link it
            VcsConnectionDTO connection = connectService.linkToCodecrowWorkspace(
                    installation.getId(), workspaceId);
            
            Map<String, Object> response = new HashMap<>();
            response.put("success", true);
            response.put("connectionId", connection.id());
            response.put("workspaceSlug", installation.getBitbucketWorkspaceSlug());
            
            return ResponseEntity.ok(response);
            
        } catch (Exception e) {
            log.error("Error linking installation", e);
            return ResponseEntity.status(HttpStatus.INTERNAL_SERVER_ERROR)
                    .body(Map.of("error", e.getMessage()));
        }
    }
    
    private String generateHandshakePage(String clientKey, BitbucketConnectInstallation installation) {
        // Redirect to CodeCrow frontend with clientKey for workspace selection
        String redirectUrl = getFrontendUrl() + "/dashboard/integrations/bitbucket/connect?clientKey=" + 
                URLEncoder.encode(clientKey, StandardCharsets.UTF_8) +
                "&workspace=" + URLEncoder.encode(installation.getBitbucketWorkspaceSlug(), StandardCharsets.UTF_8);
        
        return """
            <!DOCTYPE html>
            <html>
            <head>
                <title>Setup CodeCrow</title>
                <script src="https://connect-cdn.atl-paas.net/all.js"></script>
                <style>
                    body { font-family: -apple-system, BlinkMacSystemFont, 'Segoe UI', Roboto, sans-serif; 
                           display: flex; justify-content: center; align-items: center; 
                           min-height: 400px; margin: 0; background: #f4f5f7; }
                    .container { text-align: center; padding: 40px; background: white; 
                                 border-radius: 8px; box-shadow: 0 2px 10px rgba(0,0,0,0.1); max-width: 500px; }
                    h1 { color: #172b4d; margin-bottom: 16px; font-size: 24px; }
                    p { color: #5e6c84; margin-bottom: 24px; line-height: 1.5; }
                    .btn { display: inline-block; padding: 14px 28px; background: #0052cc; 
                           color: white; text-decoration: none; border-radius: 4px; 
                           font-weight: 600; font-size: 16px; }
                    .btn:hover { background: #0747a6; }
                    .url-box { background: #f4f5f7; padding: 12px; border-radius: 4px; 
                               word-break: break-all; font-size: 12px; color: #5e6c84; 
                               margin-top: 20px; text-align: left; }
                    .url-box a { color: #0052cc; }
                </style>
            </head>
            <body>
                <div class="container">
                    <h1>🎉 CodeCrow App Installed!</h1>
                    <p>Your Bitbucket workspace "<strong>%s</strong>" is ready to connect to CodeCrow for AI-powered code reviews.</p>
                    <a href="%s" target="_blank" class="btn">Complete Setup in CodeCrow →</a>
                    <div class="url-box">
                        <strong>Or copy this link:</strong><br/>
                        <a href="%s" target="_blank">%s</a>
                    </div>
                </div>
                <script>
                    // Initialize Atlassian Connect and resize iframe
                    if (typeof AP !== 'undefined') {
                        AP.resize('100%%', '500px');
                    }
                </script>
            </body>
            </html>
            """.formatted(installation.getBitbucketWorkspaceName(), redirectUrl, redirectUrl, redirectUrl);
    }
    
    private String generateAlreadyLinkedPage(BitbucketConnectInstallation installation) {
        return """
            <!DOCTYPE html>
            <html>
            <head>
                <title>CodeCrow Connected</title>
                <script src="https://connect-cdn.atl-paas.net/all.js"></script>
                <style>
                    body { font-family: -apple-system, BlinkMacSystemFont, 'Segoe UI', Roboto, sans-serif; 
                           display: flex; justify-content: center; align-items: center; 
                           min-height: 300px; margin: 0; background: #f4f5f7; }
                    .container { text-align: center; padding: 40px; background: white; 
                                 border-radius: 8px; box-shadow: 0 2px 10px rgba(0,0,0,0.1); }
                    h1 { color: #172b4d; margin-bottom: 16px; }
                    p { color: #5e6c84; }
                    .success { color: #00875a; font-size: 48px; margin-bottom: 16px; }
                </style>
            </head>
            <body>
                <div class="container">
                    <div class="success">✓</div>
                    <h1>Already Connected!</h1>
                    <p>Your Bitbucket workspace "<strong>%s</strong>" is already linked to CodeCrow.</p>
                </div>
                <script>
                    // Initialize Atlassian Connect and resize iframe
                    if (typeof AP !== 'undefined') {
                        AP.resize('100%%', '400px');
                    }
                </script>
            </body>
            </html>
            """.formatted(installation.getBitbucketWorkspaceName());
    }
    
    private String generateErrorPage(String message) {
        return """
            <!DOCTYPE html>
            <html>
            <head>
                <title>Error - CodeCrow</title>
                <script src="https://connect-cdn.atl-paas.net/all.js"></script>
                <style>
                    body { font-family: -apple-system, BlinkMacSystemFont, 'Segoe UI', Roboto, sans-serif; 
                           display: flex; justify-content: center; align-items: center; 
                           min-height: 300px; margin: 0; background: #f4f5f7; }
                    .container { text-align: center; padding: 40px; background: white; 
                                 border-radius: 8px; box-shadow: 0 2px 10px rgba(0,0,0,0.1); }
                    h1 { color: #de350b; margin-bottom: 16px; }
                    p { color: #5e6c84; }
                </style>
            </head>
            <body>
                <div class="container">
                    <h1>⚠️ Error</h1>
                    <p>%s</p>
                </div>
                <script>
                    // Initialize Atlassian Connect and resize iframe
                    if (typeof AP !== 'undefined') {
                        AP.resize('100%%', '400px');
                    }
                </script>
            </body>
            </html>
            """.formatted(message);
    }

    // ==================== Installation Management ====================
    
    /**
     * Get unlinked installations that the current user can access.
     * Only returns installations from Bitbucket workspaces where:
     * 1. The user was the one who installed the app (matched by installedByUsername)
     * 2. OR the user has an existing connection to that Bitbucket workspace
     * 
     * This prevents users from seeing/linking installations they don't own.
     */
    @GetMapping("/installations/unlinked")
    @PreAuthorize("isAuthenticated()")
    public ResponseEntity<List<Map<String, Object>>> getUnlinkedInstallations(
            @AuthenticationPrincipal UserDetailsImpl userDetails) {
        
        // Get installations that the current user can access
        List<BitbucketConnectInstallation> installations = 
                connectService.getUnlinkedInstallationsForUser(userDetails.getId());
        
        List<Map<String, Object>> response = installations.stream()
                .map(this::toInstallationDto)
                .toList();
        
        return ResponseEntity.ok(response);
    }
    
    /**
     * Get installations for a specific CodeCrow workspace.
     */
    @GetMapping("/installations/workspace/{workspaceId}")
    public ResponseEntity<List<Map<String, Object>>> getWorkspaceInstallations(
            @PathVariable Long workspaceId) {
        
        List<BitbucketConnectInstallation> installations = 
                connectService.getInstallationsForWorkspace(workspaceId);
        
        List<Map<String, Object>> response = installations.stream()
                .map(this::toInstallationDto)
                .toList();
        
        return ResponseEntity.ok(response);
    }
    
    /**
     * Link a Bitbucket Connect App installation to a CodeCrow workspace.
     * This creates a VCS connection using workspace-level access.
     * 
     * Security: User must have access to the Bitbucket workspace being linked
     * (verified by checking their existing VCS connections).
     */
    @PostMapping("/installations/{installationId}/link")
    @PreAuthorize("isAuthenticated() && @workspaceSecurity.hasOwnerOrAdminRights(#workspaceId, authentication)")
    public ResponseEntity<VcsConnectionDTO> linkInstallation(
            @PathVariable Long installationId,
            @RequestParam Long workspaceId,
            @AuthenticationPrincipal UserDetailsImpl userDetails) {
        
        try {
            // Verify user has access to this installation's Bitbucket workspace
            if (!connectService.canUserAccessInstallation(userDetails.getId(), installationId)) {
                log.warn("User {} attempted to link installation {} without access", 
                        userDetails.getId(), installationId);
                return ResponseEntity.status(HttpStatus.FORBIDDEN).build();
            }
            
            VcsConnectionDTO connection = connectService.linkToCodecrowWorkspace(installationId, workspaceId);
            return ResponseEntity.ok(connection);
            
        } catch (IntegrationException e) {
            log.warn("Failed to link installation: {}", e.getMessage());
            return ResponseEntity.badRequest().build();
            
        } catch (GeneralSecurityException | IOException e) {
            log.error("Error linking installation", e);
            return ResponseEntity.status(HttpStatus.INTERNAL_SERVER_ERROR).build();
        }
    }
    
    /**
     * Check if Connect App is configured and available.
     */
    @GetMapping("/status")
    public ResponseEntity<Map<String, Object>> getStatus() {
        Map<String, Object> status = new HashMap<>();
        status.put("configured", connectService.isConfigured());
        
        return ResponseEntity.ok(status);
    }
    
    // ==================== Helper Methods ====================
    
    private String extractClientKeyFromJwt(String authHeader) {
        if (authHeader == null || !authHeader.startsWith("JWT ")) {
            return null;
        }
        
        String jwt = authHeader.substring(4);
        Claims claims = connectService.verifyJwt(jwt);
        return claims != null ? claims.getIssuer() : null;
    }
    
    private Map<String, Object> toInstallationDto(BitbucketConnectInstallation installation) {
        Map<String, Object> dto = new HashMap<>();
        dto.put("id", installation.getId());
        dto.put("clientKey", installation.getClientKey());
        dto.put("bitbucketWorkspaceUuid", installation.getBitbucketWorkspaceUuid());
        dto.put("bitbucketWorkspaceSlug", installation.getBitbucketWorkspaceSlug());
        dto.put("bitbucketWorkspaceName", installation.getBitbucketWorkspaceName());
        dto.put("installedByUsername", installation.getInstalledByUsername());
        dto.put("installedAt", installation.getInstalledAt());
        dto.put("enabled", installation.isEnabled());
        dto.put("linkedWorkspaceId", 
                installation.getCodecrowWorkspace() != null 
                        ? installation.getCodecrowWorkspace().getId() 
                        : null);
        dto.put("hasVcsConnection", installation.getVcsConnection() != null);
        return dto;
    }
    
    private void cleanupOldPendingInstalls() {
        long now = System.currentTimeMillis();
        pendingInstalls.entrySet().removeIf(entry -> 
            now - entry.getValue().createdAt > 600_000 // 10 minutes
        );
    }
    
    /**
     * Helper class to track pending installations.
     */
    private static class PendingInstall {
        final Long workspaceId;
        final String workspaceSlug;
        final long createdAt;
        volatile boolean completed = false;
        volatile Long installationId;
        volatile Long connectionId;
        volatile String bitbucketWorkspaceSlug;
        
        PendingInstall(Long workspaceId, String workspaceSlug, long createdAt) {
            this.workspaceId = workspaceId;
            this.workspaceSlug = workspaceSlug;
            this.createdAt = createdAt;
        }
    }
}
