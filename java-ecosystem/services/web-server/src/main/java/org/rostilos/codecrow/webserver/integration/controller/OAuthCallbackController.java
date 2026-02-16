package org.rostilos.codecrow.webserver.integration.controller;

import org.rostilos.codecrow.webserver.generic.dto.message.MessageResponse;
import org.rostilos.codecrow.core.model.vcs.EVcsProvider;
import org.rostilos.codecrow.webserver.integration.dto.response.VcsConnectionDTO;
import org.rostilos.codecrow.webserver.exception.IntegrationException;
import org.rostilos.codecrow.webserver.integration.service.OAuthStateService;
import org.rostilos.codecrow.webserver.integration.service.VcsIntegrationService;
import org.rostilos.codecrow.webserver.workspace.service.WorkspaceService;
import org.rostilos.codecrow.core.service.SiteSettingsProvider;
import org.slf4j.Logger;
import org.slf4j.LoggerFactory;
import org.springframework.http.HttpStatus;
import org.springframework.http.MediaType;
import org.springframework.http.ResponseEntity;
import org.springframework.web.bind.annotation.*;

import com.fasterxml.jackson.databind.JsonNode;
import com.fasterxml.jackson.databind.ObjectMapper;

import java.io.IOException;
import java.net.URI;
import java.nio.charset.StandardCharsets;
import java.security.GeneralSecurityException;
import java.util.Map;

import javax.crypto.Mac;
import javax.crypto.spec.SecretKeySpec;

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
    private final SiteSettingsProvider siteSettingsProvider;
    private final ObjectMapper objectMapper;
    
    public OAuthCallbackController(
            VcsIntegrationService integrationService,
            OAuthStateService oAuthStateService,
            WorkspaceService workspaceService,
            SiteSettingsProvider siteSettingsProvider,
            ObjectMapper objectMapper
    ) {
        this.integrationService = integrationService;
        this.oAuthStateService = oAuthStateService;
        this.workspaceService = workspaceService;
        this.siteSettingsProvider = siteSettingsProvider;
        this.objectMapper = objectMapper;
    }
    
    private String getFrontendUrl() {
        return siteSettingsProvider.getBaseUrlSettings().frontendUrl();
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
            String redirectUrl = getFrontendUrl() + "/workspace?error=" + error;
            return ResponseEntity.status(HttpStatus.FOUND)
                    .location(URI.create(redirectUrl))
                    .build();
        }
        
        if (installationId != null) {
            log.info("GitHub App installation callback: installation_id={}, setup_action={}", 
                    installationId, setupAction);
            
            if (state == null) {
                log.warn("GitHub App installation without state parameter - installation_id={}", installationId);
                // No state = org owner approved installation (not the original requester).
                // They may not have a CodeCrow account. Redirect to a public success page.
                String redirectUrl = getFrontendUrl() + "/github/app-installed?installation_id=" + installationId;
                return ResponseEntity.status(HttpStatus.FOUND)
                        .location(URI.create(redirectUrl))
                        .build();
            }
            
            try {
                Long workspaceId = extractWorkspaceIdFromState(state);
                
                if (workspaceId == null) {
                    log.error("Could not extract workspace ID from state: {}", state);
                    String redirectUrl = getFrontendUrl() + "/workspace?error=invalid_state";
                    return ResponseEntity.status(HttpStatus.FOUND)
                            .location(URI.create(redirectUrl))
                            .build();
                }
                
                // Get workspace slug for the redirect URL
                String workspaceSlug = getWorkspaceSlug(workspaceId);
                
                VcsConnectionDTO connection = integrationService.handleGitHubAppInstallation(
                        installationId, workspaceId);
                
                String redirectUrl = getFrontendUrl() + "/dashboard/" + workspaceSlug + "/projects/import?connectionId=" + connection.id() + "&provider=github&connectionType=APP";
                return ResponseEntity.status(HttpStatus.FOUND)
                        .location(URI.create(redirectUrl))
                        .build();
                        
            } catch (Exception e) {
                log.error("Failed to handle GitHub App installation callback", e);
                String redirectUrl = getFrontendUrl() + "/workspace?error=installation_failed";
                return ResponseEntity.status(HttpStatus.FOUND)
                        .location(URI.create(redirectUrl))
                        .build();
            }
        }
        
        // ── Handle setup_action=request (org member requested installation, pending owner approval) ──
        if ("request".equals(setupAction)) {
            log.info("GitHub App installation request callback: setup_action=request (pending org owner approval)");
            
            if (state == null) {
                String redirectUrl = getFrontendUrl() + "/workspace?error=missing_state";
                return ResponseEntity.status(HttpStatus.FOUND)
                        .location(URI.create(redirectUrl))
                        .build();
            }
            
            try {
                Long workspaceId = extractWorkspaceIdFromState(state);
                if (workspaceId == null) {
                    String redirectUrl = getFrontendUrl() + "/workspace?error=invalid_state";
                    return ResponseEntity.status(HttpStatus.FOUND)
                            .location(URI.create(redirectUrl))
                            .build();
                }
                
                String workspaceSlug = getWorkspaceSlug(workspaceId);
                
                // Create a PENDING connection so the user can see the request status
                VcsConnectionDTO pending = integrationService.handleGitHubAppInstallationRequest(workspaceId);
                
                // Redirect to hosting settings page with a pending flag
                String redirectUrl = getFrontendUrl() + "/dashboard/" + workspaceSlug 
                        + "/hosting?provider=github&pending=true&connectionId=" + pending.id();
                return ResponseEntity.status(HttpStatus.FOUND)
                        .location(URI.create(redirectUrl))
                        .build();
                
            } catch (Exception e) {
                log.error("Failed to handle GitHub App installation request", e);
                String redirectUrl = getFrontendUrl() + "/workspace?error=request_failed";
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
                String redirectUrl = getFrontendUrl() + "/workspace?error=invalid_state";
                return ResponseEntity.status(HttpStatus.FOUND)
                        .location(URI.create(redirectUrl))
                        .build();
            }
            
            // Get workspace slug for the redirect URL
            String workspaceSlug = getWorkspaceSlug(workspaceId);
            
            VcsConnectionDTO connection = integrationService.handleAppCallback(
                    EVcsProvider.GITHUB, code, state, workspaceId);
            
            // Redirect to frontend configure page for the new connection
            String redirectUrl = getFrontendUrl() + "/dashboard/" + workspaceSlug + "/projects/import?connectionId=" + connection.id() + "&provider=github&connectionType=APP";
            return ResponseEntity.status(HttpStatus.FOUND)
                    .location(URI.create(redirectUrl))
                    .build();
            
        } catch (GeneralSecurityException | IOException e) {
            log.error("Failed to handle GitHub OAuth callback", e);
            String redirectUrl = getFrontendUrl() + "/workspace?error=callback_failed";
            return ResponseEntity.status(HttpStatus.FOUND)
                    .location(URI.create(redirectUrl))
                    .build();
        } catch (Exception e) {
            log.error("Unexpected error during GitHub OAuth callback", e);
            String redirectUrl = getFrontendUrl() + "/workspace?error=" + e.getMessage();
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
            String redirectUrl = getFrontendUrl() + "/workspace?error=" + error;
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
                String redirectUrl = getFrontendUrl() + "/workspace?error=invalid_state";
                return ResponseEntity.status(HttpStatus.FOUND)
                        .location(URI.create(redirectUrl))
                        .build();
            }
            
            // Get workspace slug for the redirect URL
            String workspaceSlug = getWorkspaceSlug(workspaceId);
            
            VcsConnectionDTO connection = integrationService.handleAppCallback(
                    EVcsProvider.BITBUCKET_CLOUD, code, state, workspaceId);
            
            // Redirect to frontend configure page for the new connection
            String redirectUrl = getFrontendUrl() + "/dashboard/" + workspaceSlug + "/projects/import?connectionId=" + connection.id() + "&provider=bitbucket-cloud&connectionType=APP";
            return ResponseEntity.status(HttpStatus.FOUND)
                    .location(URI.create(redirectUrl))
                    .build();
            
        } catch (GeneralSecurityException | IOException e) {
            log.error("Failed to handle Bitbucket OAuth callback", e);
            String redirectUrl = getFrontendUrl() + "/workspace?error=callback_failed";
            return ResponseEntity.status(HttpStatus.FOUND)
                    .location(URI.create(redirectUrl))
                    .build();
        } catch (Exception e) {
            log.error("Unexpected error during Bitbucket OAuth callback", e);
            String redirectUrl = getFrontendUrl() + "/workspace?error=" + e.getMessage();
            return ResponseEntity.status(HttpStatus.FOUND)
                    .location(URI.create(redirectUrl))
                    .build();
        }
    }
    
    /**
     * Handle OAuth callback from GitLab.
     * The workspace ID is extracted from the state parameter.
     * Supports both GitLab.com and self-hosted GitLab instances.
     * 
     * GET /api/integrations/gitlab/app/callback
     */
    @GetMapping("/gitlab/app/callback")
    public ResponseEntity<?> handleGitLabCallback(
            @RequestParam(required = false) String code,
            @RequestParam(required = false) String state,
            @RequestParam(required = false) String error,
            @RequestParam(name = "error_description", required = false) String errorDescription
    ) {
        if (error != null) {
            log.warn("GitLab OAuth callback error: {} - {}", error, errorDescription);
            String redirectUrl = getFrontendUrl() + "/workspace?error=" + error;
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
            // Validate state and extract workspace ID
            Long workspaceId = extractWorkspaceIdFromState(state);
            
            if (workspaceId == null) {
                log.error("Could not extract workspace ID from GitLab OAuth state: {}", state);
                String redirectUrl = getFrontendUrl() + "/workspace?error=invalid_state";
                return ResponseEntity.status(HttpStatus.FOUND)
                        .location(URI.create(redirectUrl))
                        .build();
            }
            
            // Get workspace slug for the redirect URL
            String workspaceSlug = getWorkspaceSlug(workspaceId);
            
            // Handle the OAuth callback and create/update the connection
            VcsConnectionDTO connection = integrationService.handleAppCallback(
                    EVcsProvider.GITLAB, code, state, workspaceId);
            
            log.info("GitLab OAuth successful for workspace {} (connection: {})", 
                    workspaceSlug, connection.id());
            
            // Redirect to frontend project import page with the new connection
            String redirectUrl = getFrontendUrl() + "/dashboard/" + workspaceSlug + 
                    "/projects/import?connectionId=" + connection.id() + 
                    "&provider=gitlab&connectionType=APP";
            return ResponseEntity.status(HttpStatus.FOUND)
                    .location(URI.create(redirectUrl))
                    .build();
            
        } catch (GeneralSecurityException | IOException e) {
            log.error("Failed to handle GitLab OAuth callback", e);
            String redirectUrl = getFrontendUrl() + "/workspace?error=callback_failed";
            return ResponseEntity.status(HttpStatus.FOUND)
                    .location(URI.create(redirectUrl))
                    .build();
        } catch (Exception e) {
            log.error("Unexpected error during GitLab OAuth callback", e);
            String redirectUrl = getFrontendUrl() + "/workspace?error=" + e.getMessage();
            return ResponseEntity.status(HttpStatus.FOUND)
                    .location(URI.create(redirectUrl))
                    .build();
        }
    }

    /**
     * Receive GitHub App-level webhooks (installation lifecycle events).
     * 
     * GitHub sends these events to the App's webhook URL when:
     * - An org owner approves a pending installation request (installation.created)
     * - A user directly installs the App (installation.created)
     * - The installation is updated (installation.new_permissions_accepted)
     * - The installation is removed (installation.deleted)
     * 
     * This endpoint does NOT require authentication — the org owner who approves
     * the installation may not have a CodeCrow account at all.
     * 
     * POST /api/integrations/github/app/webhook
     */
    @PostMapping(value = "/github/app/webhook", consumes = MediaType.APPLICATION_JSON_VALUE)
    public ResponseEntity<?> handleGitHubAppWebhook(
            @RequestHeader(value = "X-GitHub-Event", required = false) String eventType,
            @RequestHeader(value = "X-Hub-Signature-256", required = false) String signatureHeader,
            @RequestBody String body
    ) {
        try {
            // ── Verify webhook signature (HMAC-SHA256) ──
            String webhookSecret = siteSettingsProvider.getGitHubSettings().webhookSecret();
            if (webhookSecret != null && !webhookSecret.isBlank()) {
                if (signatureHeader == null || signatureHeader.isBlank()) {
                    log.warn("Rejected GitHub App webhook: missing X-Hub-Signature-256 header");
                    return ResponseEntity.status(HttpStatus.UNAUTHORIZED)
                            .body(Map.of("error", "Missing signature"));
                }
                if (!verifyGitHubSignature(body, signatureHeader, webhookSecret)) {
                    log.warn("Rejected GitHub App webhook: invalid signature");
                    return ResponseEntity.status(HttpStatus.UNAUTHORIZED)
                            .body(Map.of("error", "Invalid signature"));
                }
            } else {
                log.warn("GitHub App webhook secret not configured — skipping signature verification. " +
                        "Set GITHUB_WEBHOOK_SECRET for production security.");
            }
            
            JsonNode payload = objectMapper.readTree(body);
            String action = payload.path("action").asText(null);
            
            log.info("Received GitHub App webhook: event={}, action={}", eventType, action);
            
            if (!"installation".equals(eventType)) {
                log.debug("Ignoring non-installation GitHub App webhook: {}", eventType);
                return ResponseEntity.ok(Map.of("status", "ignored", "event", String.valueOf(eventType)));
            }
            
            if ("created".equals(action)) {
                // Installation was approved (by org owner) or directly installed
                JsonNode installation = payload.path("installation");
                long installationId = installation.path("id").asLong();
                String accountLogin = installation.path("account").path("login").asText(null);
                String accountType = installation.path("account").path("type").asText(null);
                
                log.info("GitHub App installation created: id={}, account={} ({})", 
                        installationId, accountLogin, accountType);
                
                try {
                    VcsConnectionDTO connection = integrationService.completeGitHubAppInstallation(
                            installationId, accountLogin, accountType);
                    
                    log.info("Completed GitHub App installation via webhook: connectionId={}, status={}", 
                            connection.id(), connection.status());
                    
                    return ResponseEntity.ok(Map.of(
                            "status", "completed",
                            "connectionId", connection.id(),
                            "installationId", installationId
                    ));
                } catch (Exception e) {
                    log.error("Failed to complete GitHub App installation for {}: {}", 
                            installationId, e.getMessage(), e);
                    return ResponseEntity.ok(Map.of(
                            "status", "error",
                            "message", e.getMessage()
                    ));
                }
                
            } else if ("deleted".equals(action) || "suspend".equals(action)) {
                JsonNode installation = payload.path("installation");
                long installationId = installation.path("id").asLong();
                
                log.info("GitHub App installation {}: id={}", action, installationId);
                
                integrationService.handleGitHubAppInstallationRemoved(installationId);
                
                return ResponseEntity.ok(Map.of("status", "processed", "action", action));
                
            } else {
                log.debug("Ignoring installation action: {}", action);
                return ResponseEntity.ok(Map.of("status", "ignored", "action", String.valueOf(action)));
            }
            
        } catch (Exception e) {
            log.error("Error processing GitHub App webhook", e);
            return ResponseEntity.ok(Map.of("status", "error", "message", e.getMessage()));
        }
    }

    /**
     * Verify GitHub webhook signature using HMAC-SHA256.
     * GitHub sends the signature as "sha256=<hex-digest>" in X-Hub-Signature-256.
     */
    private boolean verifyGitHubSignature(String payload, String signatureHeader, String secret) {
        try {
            if (!signatureHeader.startsWith("sha256=")) {
                return false;
            }
            String expectedSignature = signatureHeader.substring("sha256=".length());
            
            Mac mac = Mac.getInstance("HmacSHA256");
            mac.init(new SecretKeySpec(secret.getBytes(StandardCharsets.UTF_8), "HmacSHA256"));
            byte[] hash = mac.doFinal(payload.getBytes(StandardCharsets.UTF_8));
            
            // Convert to hex
            StringBuilder hexString = new StringBuilder();
            for (byte b : hash) {
                String hex = Integer.toHexString(0xff & b);
                if (hex.length() == 1) hexString.append('0');
                hexString.append(hex);
            }
            
            // Constant-time comparison to prevent timing attacks
            return java.security.MessageDigest.isEqual(
                    expectedSignature.getBytes(StandardCharsets.UTF_8),
                    hexString.toString().getBytes(StandardCharsets.UTF_8));
        } catch (Exception e) {
            log.error("Failed to verify GitHub webhook signature", e);
            return false;
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
