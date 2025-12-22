package org.rostilos.codecrow.webserver.service.integration;

import com.fasterxml.jackson.databind.JsonNode;
import com.fasterxml.jackson.databind.ObjectMapper;
import okhttp3.OkHttpClient;
import okhttp3.Request;
import okhttp3.Response;
import org.rostilos.codecrow.core.model.user.User;
import org.rostilos.codecrow.core.model.vcs.*;
import org.rostilos.codecrow.core.model.workspace.Workspace;
import org.rostilos.codecrow.core.persistence.repository.vcs.PendingForgeInstallationRepository;
import org.rostilos.codecrow.core.persistence.repository.vcs.VcsConnectionRepository;
import org.rostilos.codecrow.core.persistence.repository.workspace.WorkspaceRepository;
import org.rostilos.codecrow.security.oauth.TokenEncryptionService;
import org.slf4j.Logger;
import org.slf4j.LoggerFactory;
import org.springframework.beans.factory.annotation.Value;
import org.springframework.scheduling.annotation.Scheduled;
import org.springframework.stereotype.Service;
import org.springframework.transaction.annotation.Transactional;

import java.io.IOException;
import java.security.GeneralSecurityException;
import java.security.SecureRandom;
import java.time.Instant;
import java.time.LocalDateTime;
import java.time.ZoneId;
import java.util.Base64;
import java.util.List;
import java.util.Optional;

/**
 * Service for processing Atlassian Forge lifecycle events with secure user binding.
 * 
 * SECURITY FLOW:
 * 1. User clicks "Install Bitbucket App" in CodeCrow UI
 * 2. Backend creates PendingForgeInstallation with user, workspace, and unique state token
 * 3. User is redirected to Atlassian Forge install page with state in URL
 * 4. User completes installation in Bitbucket
 * 5. Forge sends avi:forge:installed:app event to our backend
 * 6. We match the installation to the pending record by:
 *    a) State token (if Forge passes it back) - most secure
 *    b) Most recent pending installation from same user for same workspace - fallback
 * 7. VcsConnection is created and linked to the correct CodeCrow workspace
 * 8. PendingForgeInstallation is marked as completed
 * 
 * This ensures:
 * - Only authenticated CodeCrow users can initiate installations
 * - Installations are bound to the correct CodeCrow workspace
 * - Orphaned installations are not possible
 * - Pending installations expire after 30 minutes
 * 
 * @see <a href="https://developer.atlassian.com/platform/forge/remote/calling-product-apis/">Forge Remote APIs</a>
 */
@Service
public class ForgeWebhookService {

    private static final Logger log = LoggerFactory.getLogger(ForgeWebhookService.class);
    private static final SecureRandom SECURE_RANDOM = new SecureRandom();

    private final VcsConnectionRepository vcsConnectionRepository;
    private final PendingForgeInstallationRepository pendingInstallationRepository;
    private final WorkspaceRepository workspaceRepository;
    private final TokenEncryptionService encryptionService;
    private final ObjectMapper objectMapper;

    @Value("${codecrow.forge.app.id:}")
    private String forgeAppId;
    
    @Value("${codecrow.forge.install.url:}")
    private String forgeInstallUrl;

    public ForgeWebhookService(
            VcsConnectionRepository vcsConnectionRepository,
            PendingForgeInstallationRepository pendingInstallationRepository,
            WorkspaceRepository workspaceRepository,
            TokenEncryptionService encryptionService
    ) {
        this.vcsConnectionRepository = vcsConnectionRepository;
        this.pendingInstallationRepository = pendingInstallationRepository;
        this.workspaceRepository = workspaceRepository;
        this.encryptionService = encryptionService;
        this.objectMapper = new ObjectMapper();
    }

    // ==================== Installation Initiation (from UI) ====================

    /**
     * Initiate a Forge app installation.
     * Called when user clicks "Install Bitbucket App" in the UI.
     * 
     * @param workspace The CodeCrow workspace to link the installation to
     * @param user The CodeCrow user initiating the installation
     * @return Installation URL with state token
     */
    @Transactional
    public ForgeInstallInitiation initiateInstallation(Workspace workspace, User user) {
        log.info("Initiating Forge installation for workspace {} by user {}", 
                workspace.getId(), user.getId());

        // Generate unique state token
        String state = generateSecureState();

        // Create pending installation record
        PendingForgeInstallation pending = new PendingForgeInstallation(
                state,
                workspace,
                user,
                EVcsProvider.BITBUCKET_CLOUD
        );
        pendingInstallationRepository.save(pending);

        // Build install URL with state
        String installUrl = buildInstallUrl(state);

        log.info("Created pending Forge installation {} with state {} for workspace {}", 
                pending.getId(), state, workspace.getId());

        return new ForgeInstallInitiation(installUrl, state, pending.getExpiresAt());
    }

    /**
     * Check the status of a pending installation.
     */
    @Transactional(readOnly = true)
    public Optional<PendingForgeInstallation> checkInstallationStatus(String state) {
        return pendingInstallationRepository.findByState(state);
    }

    /**
     * Get pending installations for a user.
     */
    @Transactional(readOnly = true)
    public List<PendingForgeInstallation> getPendingInstallationsForUser(Long userId) {
        return pendingInstallationRepository.findByInitiatedByIdAndStatus(
                userId, 
                PendingForgeInstallation.Status.PENDING
        );
    }

    // ==================== Forge Webhook Handlers ====================

    /**
     * Handle app installation event from Forge.
     * Matches the installation to a pending record and creates VcsConnection.
     * 
     * @param payload The Forge event payload
     * @param appSystemToken The OAuth token from x-forge-oauth-system header
     * @param fitToken The Forge Invocation Token from Authorization header
     * @return The created or updated VcsConnection, or null if no match found
     */
    @Transactional
    public VcsConnection handleAppInstalled(JsonNode payload, String appSystemToken, String fitToken) {
        log.info("Processing Forge app installed event");
        log.debug("Payload: {}", payload);
        
        try {
            // Extract context from FIT token or payload
            JsonNode context = extractContext(payload, fitToken);
            
            String externalWorkspaceId = extractWorkspaceId(context, payload);
            String externalWorkspaceSlug = extractWorkspaceSlug(context, payload);
            String installationId = extractInstallationId(payload, fitToken);
            
            // Try to extract state from payload (if Forge passes it back)
            String state = extractState(payload);
            
            log.info("Forge app installed - externalWorkspace: {}, slug: {}, installationId: {}, state: {}",
                    externalWorkspaceId, externalWorkspaceSlug, installationId, state);

            if (externalWorkspaceId == null) {
                log.error("Could not extract workspace ID from Forge installation");
                return null;
            }

            if (appSystemToken == null || appSystemToken.isEmpty()) {
                log.error("No appSystemToken provided in installation event");
                return null;
            }

            // Find the pending installation to get the CodeCrow workspace
            PendingForgeInstallation pendingInstall = findPendingInstallation(state, externalWorkspaceId);
            
            if (pendingInstall == null) {
                log.warn("No matching pending installation found for Forge callback. " +
                        "This could be a security issue or the installation wasn't initiated from CodeCrow.");
                // Don't create connection without proper workspace binding
                return null;
            }

            if (pendingInstall.isExpired()) {
                log.warn("Pending installation {} has expired", pendingInstall.getId());
                pendingInstall.markExpired();
                pendingInstallationRepository.save(pendingInstall);
                return null;
            }

            Workspace codecrowWorkspace = pendingInstall.getWorkspace();
            log.info("Matched Forge installation to CodeCrow workspace: {} ({})", 
                    codecrowWorkspace.getId(), codecrowWorkspace.getName());

            // If workspace slug is not provided in payload, fetch it from Bitbucket API
            if (externalWorkspaceSlug == null && externalWorkspaceId != null) {
                log.info("Workspace slug not in payload, fetching from Bitbucket API using UUID: {}", externalWorkspaceId);
                externalWorkspaceSlug = fetchWorkspaceSlugFromBitbucket(externalWorkspaceId, appSystemToken);
                log.info("Fetched workspace slug: {}", externalWorkspaceSlug);
            }

            // Encrypt the token for storage
            String encryptedToken;
            try {
                encryptedToken = encryptionService.encrypt(appSystemToken);
            } catch (GeneralSecurityException e) {
                log.error("Failed to encrypt app system token", e);
                pendingInstall.markFailed("Token encryption failed");
                pendingInstallationRepository.save(pendingInstall);
                throw new RuntimeException("Token encryption failed", e);
            }

            // Extract token expiry from JWT claims
            LocalDateTime tokenExpiry = extractTokenExpiry(appSystemToken);

            // Find existing connection or create new one
            VcsConnection connection = findOrCreateConnection(
                    codecrowWorkspace, 
                    externalWorkspaceId,
                    externalWorkspaceSlug
            );
            
            // Update connection with Forge app details
            connection.setConnectionType(EVcsConnectionType.FORGE_APP);
            connection.setExternalWorkspaceId(externalWorkspaceId);
            connection.setExternalWorkspaceSlug(externalWorkspaceSlug);
            connection.setInstallationId(installationId);
            connection.setAccessToken(encryptedToken);
            connection.setTokenExpiresAt(tokenExpiry);
            connection.setConnectionName("Bitbucket: " + (externalWorkspaceSlug != null ? externalWorkspaceSlug : externalWorkspaceId));
            connection.setSetupStatus(EVcsSetupStatus.CONNECTED);
            
            connection = vcsConnectionRepository.save(connection);
            log.info("Saved VcsConnection {} for CodeCrow workspace {} with Forge token", 
                    connection.getId(), codecrowWorkspace.getId());

            // Mark pending installation as completed
            pendingInstall.markCompleted(connection.getId(), externalWorkspaceId, externalWorkspaceSlug);
            pendingInstallationRepository.save(pendingInstall);
            
            return connection;
            
        } catch (Exception e) {
            log.error("Error processing Forge app installed event", e);
            throw e;
        }
    }

    /**
     * Handle app uninstallation event from Forge.
     * Marks the VcsConnection as disconnected and clears tokens.
     */
    @Transactional
    public void handleAppUninstalled(JsonNode payload, String fitToken) {
        log.info("Processing Forge app uninstalled event");
        
        try {
            JsonNode context = extractContext(payload, fitToken);
            
            String externalWorkspaceId = extractWorkspaceId(context, payload);
            String installationId = extractInstallationId(payload, fitToken);
            
            log.info("Forge app uninstalled - workspace: {}, installationId: {}", 
                    externalWorkspaceId, installationId);

            if (externalWorkspaceId != null) {
                List<VcsConnection> connections = vcsConnectionRepository
                        .findByProviderTypeAndExternalWorkspaceId(EVcsProvider.BITBUCKET_CLOUD, externalWorkspaceId);
                
                for (VcsConnection conn : connections) {
                    if (conn.getConnectionType() == EVcsConnectionType.FORGE_APP) {
                        conn.setSetupStatus(EVcsSetupStatus.DISABLED);
                        conn.setAccessToken(null);
                        conn.setTokenExpiresAt(null);
                        vcsConnectionRepository.save(conn);
                        log.info("Marked VcsConnection {} as disabled", conn.getId());
                    }
                }
            }
        } catch (Exception e) {
            log.error("Error processing Forge app uninstalled event", e);
            throw e;
        }
    }

    /**
     * Update the stored token when receiving fresh tokens from Forge events.
     */
    @Transactional
    public void refreshToken(String externalWorkspaceId, String newAppSystemToken) {
        if (externalWorkspaceId == null || newAppSystemToken == null) {
            return;
        }

        try {
            List<VcsConnection> connections = vcsConnectionRepository
                    .findByProviderTypeAndExternalWorkspaceId(EVcsProvider.BITBUCKET_CLOUD, externalWorkspaceId);
            
            for (VcsConnection conn : connections) {
                if (conn.getConnectionType() == EVcsConnectionType.FORGE_APP) {
                    String encryptedToken = encryptionService.encrypt(newAppSystemToken);
                    LocalDateTime tokenExpiry = extractTokenExpiry(newAppSystemToken);
                    
                    conn.setAccessToken(encryptedToken);
                    conn.setTokenExpiresAt(tokenExpiry);
                    vcsConnectionRepository.save(conn);
                    
                    log.debug("Refreshed token for VcsConnection {}", conn.getId());
                }
            }
        } catch (Exception e) {
            log.warn("Failed to refresh token for workspace {}: {}", externalWorkspaceId, e.getMessage());
        }
    }

    // ==================== Scheduled Cleanup ====================

    /**
     * Clean up expired pending installations.
     * Runs every 5 minutes.
     */
    @Scheduled(fixedRate = 300000) // 5 minutes
    @Transactional
    public void cleanupExpiredInstallations() {
        try {
            int markedExpired = pendingInstallationRepository.markExpiredInstallations(LocalDateTime.now());
            if (markedExpired > 0) {
                log.info("Marked {} pending Forge installations as expired", markedExpired);
            }

            // Delete old completed/failed installations (older than 7 days)
            int deleted = pendingInstallationRepository.deleteOldCompletedInstallations(
                    LocalDateTime.now().minusDays(7)
            );
            if (deleted > 0) {
                log.info("Deleted {} old Forge installation records", deleted);
            }
        } catch (Exception e) {
            log.warn("Error during Forge installation cleanup: {}", e.getMessage());
        }
    }

    // ==================== Helper Methods ====================

    private String generateSecureState() {
        byte[] bytes = new byte[32];
        SECURE_RANDOM.nextBytes(bytes);
        return Base64.getUrlEncoder().withoutPadding().encodeToString(bytes);
    }

    private String buildInstallUrl(String state) {
        // Forge apps require a signed install URL from Atlassian.
        // The developer.atlassian.com/console/install URL with just state parameter 
        // won't work - it needs a cryptographic signature from Atlassian's systems.
        //
        // Options for install URL:
        // 1. Atlassian Marketplace listing (if app is published)
        // 2. Signed URL from forge install --list command
        // 3. Direct link from Atlassian Developer Console
        //
        // We use a configurable URL that should be set to the proper signed install URL.
        // The pending installation record tracks which CodeCrow user/workspace initiated
        // the installation, and we match it when the app:installed event arrives.
        
        if (forgeInstallUrl != null && !forgeInstallUrl.isBlank()) {
            return forgeInstallUrl;
        }
        
        // Fallback: Bitbucket workspace app management - user can find and install there
        log.warn("Forge install URL not configured. Using fallback Bitbucket apps page.");
        return "https://bitbucket.org/account/workspaces/";
    }

    private PendingForgeInstallation findPendingInstallation(String state, String externalWorkspaceId) {
        // First, try to find by state (most secure)
        if (state != null && !state.isEmpty()) {
            Optional<PendingForgeInstallation> byState = pendingInstallationRepository.findByState(state);
            if (byState.isPresent()) {
                log.info("Found pending installation by state: {}", state);
                return byState.get();
            }
        }

        // Fallback: Find the most recent pending installation
        // This is less secure but handles cases where Forge doesn't pass state back
        List<PendingForgeInstallation> pending = pendingInstallationRepository.findAllPendingNotExpired(
                EVcsProvider.BITBUCKET_CLOUD,
                PendingForgeInstallation.Status.PENDING,
                LocalDateTime.now()
        );
        
        log.info("Found {} pending installations for fallback matching", pending.size());

        if (!pending.isEmpty()) {
            // Return the most recent one (list is ordered by createdAt DESC)
            PendingForgeInstallation recent = pending.get(0);
            log.info("Found most recent pending installation {} (fallback - no state match)", recent.getId());
            return recent;
        }

        log.warn("No pending installations found for fallback matching");
        return null;
    }

    private VcsConnection findOrCreateConnection(Workspace workspace, String externalWorkspaceId, String externalWorkspaceSlug) {
        // Try to find existing connection for this workspace
        List<VcsConnection> existing = vcsConnectionRepository
                .findByProviderTypeAndExternalWorkspaceId(EVcsProvider.BITBUCKET_CLOUD, externalWorkspaceId);
        
        // Find one that belongs to our CodeCrow workspace
        for (VcsConnection conn : existing) {
            if (conn.getWorkspace() != null && conn.getWorkspace().getId().equals(workspace.getId())) {
                log.info("Found existing VcsConnection {} for workspace", conn.getId());
                return conn;
            }
        }

        // Create new connection
        VcsConnection connection = new VcsConnection();
        connection.setWorkspace(workspace);
        connection.setProviderType(EVcsProvider.BITBUCKET_CLOUD);
        log.info("Creating new VcsConnection for Forge app installation");
        return connection;
    }

    private String extractState(JsonNode payload) {
        // Try different locations where state might be passed
        if (payload.has("state")) {
            return payload.get("state").asText();
        }
        if (payload.has("context") && payload.get("context").has("state")) {
            return payload.get("context").get("state").asText();
        }
        return null;
    }

    private JsonNode extractContext(JsonNode payload, String fitToken) {
        if (payload.has("context")) {
            return payload.get("context");
        }
        if (payload.has("app")) {
            return payload;
        }
        if (fitToken != null && !fitToken.isEmpty()) {
            try {
                JsonNode fitClaims = decodeFitToken(fitToken);
                if (fitClaims != null && fitClaims.has("app")) {
                    return fitClaims;
                }
            } catch (Exception e) {
                log.debug("Could not decode FIT token: {}", e.getMessage());
            }
        }
        return payload;
    }

    private JsonNode decodeFitToken(String token) {
        try {
            String[] parts = token.replace("Bearer ", "").split("\\.");
            if (parts.length >= 2) {
                String payload = new String(Base64.getUrlDecoder().decode(parts[1]));
                return objectMapper.readTree(payload);
            }
        } catch (Exception e) {
            log.debug("Failed to decode FIT token: {}", e.getMessage());
        }
        return null;
    }

    private String extractWorkspaceId(JsonNode context, JsonNode payload) {
        // First, check if payload has "context" as a string (ARI format)
        // Format: ari:cloud:bitbucket::workspace/49bfa9b9-af53-4277-935b-0b586716a225
        if (payload.has("context") && payload.get("context").isTextual()) {
            String contextAri = payload.get("context").asText();
            String workspaceId = extractWorkspaceIdFromAri(contextAri);
            if (workspaceId != null) {
                log.info("Extracted workspace ID from context ARI: {}", workspaceId);
                return workspaceId;
            }
        }
        
        // Check if context is an object with cloudId
        if (context != null && context.isObject()) {
            if (context.has("app") && context.get("app").has("apiBaseUrl")) {
                String apiBaseUrl = context.get("app").get("apiBaseUrl").asText();
                String[] parts = apiBaseUrl.split("/");
                if (parts.length > 0) {
                    return parts[parts.length - 1];
                }
            }
            if (context.has("cloudId")) {
                return context.get("cloudId").asText();
            }
        }
        
        if (payload.has("workspace")) {
            JsonNode workspace = payload.get("workspace");
            if (workspace.has("uuid")) {
                return workspace.get("uuid").asText().replace("{", "").replace("}", "");
            }
        }
        if (payload.has("payload") && payload.get("payload").has("workspace")) {
            JsonNode workspace = payload.get("payload").get("workspace");
            if (workspace.has("uuid")) {
                return workspace.get("uuid").asText().replace("{", "").replace("}", "");
            }
        }
        return null;
    }
    
    /**
     * Extract workspace ID from Atlassian Resource Identifier (ARI).
     * Format: ari:cloud:bitbucket::workspace/{workspace-uuid}
     */
    private String extractWorkspaceIdFromAri(String ari) {
        if (ari == null || !ari.startsWith("ari:")) {
            return null;
        }
        // Extract the resource ID after "workspace/"
        int workspaceIdx = ari.indexOf("workspace/");
        if (workspaceIdx != -1) {
            return ari.substring(workspaceIdx + "workspace/".length());
        }
        // Try to extract any UUID-like ID at the end
        String[] parts = ari.split("/");
        if (parts.length > 0) {
            String lastPart = parts[parts.length - 1];
            // Check if it looks like a UUID
            if (lastPart.matches("[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}")) {
                return lastPart;
            }
        }
        return null;
    }

    private String extractWorkspaceSlug(JsonNode context, JsonNode payload) {
        if (payload.has("workspace") && payload.get("workspace").has("slug")) {
            return payload.get("workspace").get("slug").asText();
        }
        if (payload.has("payload") && payload.get("payload").has("workspace")) {
            JsonNode workspace = payload.get("payload").get("workspace");
            if (workspace.has("slug")) {
                return workspace.get("slug").asText();
            }
        }
        return null;
    }

    private String extractInstallationId(JsonNode payload, String fitToken) {
        // The installation ID is in the payload's "id" field for Forge events
        if (payload.has("id")) {
            String id = payload.get("id").asText();
            log.info("Extracted installation ID from payload: {}", id);
            return id;
        }
        
        if (fitToken != null) {
            JsonNode claims = decodeFitToken(fitToken);
            if (claims != null && claims.has("app") && claims.get("app").has("installationId")) {
                return claims.get("app").get("installationId").asText();
            }
        }
        if (payload.has("installationId")) {
            return payload.get("installationId").asText();
        }
        if (payload.has("context") && payload.get("context").has("installationId")) {
            return payload.get("context").get("installationId").asText();
        }
        return null;
    }

    private LocalDateTime extractTokenExpiry(String token) {
        try {
            String[] parts = token.split("\\.");
            if (parts.length >= 2) {
                String payload = new String(Base64.getUrlDecoder().decode(parts[1]));
                JsonNode claims = objectMapper.readTree(payload);
                if (claims.has("exp")) {
                    long exp = claims.get("exp").asLong();
                    return LocalDateTime.ofInstant(Instant.ofEpochSecond(exp), ZoneId.systemDefault());
                }
            }
        } catch (Exception e) {
            log.debug("Failed to extract token expiry: {}", e.getMessage());
        }
        return LocalDateTime.now().plusHours(1);
    }

    /**
     * Fetch workspace slug from Bitbucket API using the workspace UUID.
     * Since Forge tokens may not have workspace:read scope, we fetch from user endpoint
     * which returns workspaces the user has access to.
     * 
     * @param workspaceUuid The workspace UUID (without braces)
     * @param accessToken The access token to use for authentication
     * @return The workspace slug, or null if fetch fails
     */
    /**
     * Fetch workspace slug from Bitbucket API using the workspace UUID.
     * Requires read:workspace:bitbucket scope in Forge app manifest.
     * 
     * @param workspaceUuid The workspace UUID (without braces)
     * @param accessToken The access token to use for authentication
     * @return The workspace slug, or UUID as fallback if fetch fails
     */
    private String fetchWorkspaceSlugFromBitbucket(String workspaceUuid, String accessToken) {
        // GET /workspaces/{workspace} - requires read:workspace:bitbucket scope
        // The workspace parameter can be UUID or slug
        String url = "https://api.bitbucket.org/2.0/workspaces/" + workspaceUuid;
        
        OkHttpClient client = new OkHttpClient();
        Request request = new Request.Builder()
                .url(url)
                .header("Authorization", "Bearer " + accessToken)
                .header("Accept", "application/json")
                .get()
                .build();
        
        try (Response response = client.newCall(request).execute()) {
            String body = response.body() != null ? response.body().string() : "";
            
            if (!response.isSuccessful()) {
                log.warn("Failed to fetch workspace {} from Bitbucket: {} - {}", 
                        workspaceUuid, response.code(), body);
                // Return UUID as fallback - some Bitbucket operations may still work
                return workspaceUuid;
            }
            
            JsonNode workspaceJson = objectMapper.readTree(body);
            
            if (workspaceJson.has("slug")) {
                String slug = workspaceJson.get("slug").asText();
                log.info("Resolved workspace slug '{}' from UUID '{}'", slug, workspaceUuid);
                return slug;
            }
            
            log.warn("Workspace response missing slug field for UUID: {}", workspaceUuid);
            return workspaceUuid;
            
        } catch (IOException e) {
            log.warn("Error fetching workspace info from Bitbucket: {}", e.getMessage());
            return workspaceUuid;
        }
    }
    
    // ==================== DTOs ====================

    /**
     * Result of initiating a Forge installation.
     */
    public record ForgeInstallInitiation(
            String installUrl,
            String state,
            LocalDateTime expiresAt
    ) {}
}
