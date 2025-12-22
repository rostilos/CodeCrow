package org.rostilos.codecrow.webserver.service.integration;

import com.fasterxml.jackson.databind.JsonNode;
import com.fasterxml.jackson.databind.ObjectMapper;
import io.jsonwebtoken.Claims;
import io.jsonwebtoken.Jws;
import io.jsonwebtoken.JwtException;
import io.jsonwebtoken.Jwts;
import io.jsonwebtoken.security.Keys;
import okhttp3.*;
import org.rostilos.codecrow.core.model.vcs.*;
import org.rostilos.codecrow.core.model.workspace.Workspace;
import org.rostilos.codecrow.core.persistence.repository.vcs.BitbucketConnectInstallationRepository;
import org.rostilos.codecrow.core.persistence.repository.vcs.VcsConnectionRepository;
import org.rostilos.codecrow.core.persistence.repository.workspace.WorkspaceMemberRepository;
import org.rostilos.codecrow.core.persistence.repository.workspace.WorkspaceRepository;
import org.rostilos.codecrow.security.oauth.TokenEncryptionService;
import org.rostilos.codecrow.webserver.dto.response.integration.VcsConnectionDTO;
import org.rostilos.codecrow.webserver.exception.IntegrationException;
import org.slf4j.Logger;
import org.slf4j.LoggerFactory;
import org.springframework.beans.factory.annotation.Value;
import org.springframework.stereotype.Service;
import org.springframework.transaction.annotation.Transactional;

import javax.crypto.SecretKey;
import java.io.IOException;
import java.nio.charset.StandardCharsets;
import java.security.GeneralSecurityException;
import java.time.LocalDateTime;
import java.util.List;
import java.util.Optional;

/**
 * Service for handling Bitbucket Connect App lifecycle events and JWT authentication.
 */

/**
 * Service for handling Bitbucket Connect App lifecycle events and JWT authentication.
 */
@Service
public class BitbucketConnectService {
    
    private static final Logger log = LoggerFactory.getLogger(BitbucketConnectService.class);
    private static final String BITBUCKET_TOKEN_URL = "https://bitbucket.org/site/oauth2/access_token";
    private static final MediaType FORM_MEDIA_TYPE = MediaType.parse("application/x-www-form-urlencoded");
    
    private final BitbucketConnectInstallationRepository installationRepository;
    private final VcsConnectionRepository vcsConnectionRepository;
    private final WorkspaceRepository workspaceRepository;
    private final WorkspaceMemberRepository workspaceMemberRepository;
    private final TokenEncryptionService encryptionService;
    private final ObjectMapper objectMapper;
    private final OkHttpClient httpClient;
    
    @Value("${codecrow.bitbucket.connect.client-id:}")
    private String connectClientId;
    
    @Value("${codecrow.bitbucket.connect.client-secret:}")
    private String connectClientSecret;
    
    @Value("${codecrow.web.base.url:http://localhost:8081}")
    private String baseUrl;

    @Value("${codecrow.frontend-url:http://localhost:8080}")
    private String baseFrontendUrl;
    
    public BitbucketConnectService(
            BitbucketConnectInstallationRepository installationRepository,
            VcsConnectionRepository vcsConnectionRepository,
            WorkspaceRepository workspaceRepository,
            WorkspaceMemberRepository workspaceMemberRepository,
            TokenEncryptionService encryptionService
    ) {
        this.installationRepository = installationRepository;
        this.vcsConnectionRepository = vcsConnectionRepository;
        this.workspaceRepository = workspaceRepository;
        this.workspaceMemberRepository = workspaceMemberRepository;
        this.encryptionService = encryptionService;
        this.objectMapper = new ObjectMapper();
        this.httpClient = new OkHttpClient();
    }
    
    /**
     * Handle the "installed" lifecycle event from Bitbucket.
     * This is called when a workspace admin installs the Connect App.
     * 
     * Note: The principal can be either a "workspace" or a "user" depending on
     * whether the app is installed on a workspace or a personal account.
     * 
     * For personal accounts, the actual workspace slug is different from the username/nickname,
     * so we fetch it from the Bitbucket API after saving the installation.
     */
    @Transactional
    public BitbucketConnectInstallation handleInstalled(JsonNode payload) {
        log.info("Processing Connect App installation: {}", payload);
        
        String clientKey = payload.path("clientKey").asText();
        String sharedSecret = payload.path("sharedSecret").asText();
        String baseApiUrl = payload.path("baseApiUrl").asText("https://api.bitbucket.org");
        
        // Principal info (workspace or user that installed the app)
        JsonNode principal = payload.path("principal");
        String principalType = principal.path("type").asText("user");
        String workspaceUuid = principal.path("uuid").asText();
        
        // For workspaces, "slug" or "username" contains the slug. 
        // For users (personal accounts), we need to fetch it from the API later.
        String workspaceSlug = principal.path("slug").asText("");
        if (workspaceSlug.isEmpty()) {
            workspaceSlug = principal.path("username").asText("");
        }
        
        // Temporary placeholder - will be updated with real slug from API
        String tempSlug = workspaceSlug.isEmpty() ? 
                workspaceUuid.replace("{", "").replace("}", "") : workspaceSlug;
        
        String workspaceName = principal.path("display_name").asText(tempSlug);
        
        log.info("Principal type: {}, tempSlug: {}, uuid: {}", principalType, tempSlug, workspaceUuid);
        
        // User who performed the installation
        JsonNode user = payload.path("user");
        String installedByUuid = user.has("uuid") ? user.path("uuid").asText() : null;
        String installedByUsername = user.has("nickname") ? user.path("nickname").asText() : 
                                     user.has("username") ? user.path("username").asText() : null;
        
        // OAuth credentials (if provided)
        String oauthClientId = payload.has("oauthClientId") ? payload.path("oauthClientId").asText() : null;
        String publicKey = payload.has("publicKey") ? payload.path("publicKey").asText() : null;
        String productType = payload.has("productType") ? payload.path("productType").asText() : "bitbucket";
        
        // Encrypt the shared secret for secure storage
        String encryptedSharedSecret;
        try {
            encryptedSharedSecret = encryptionService.encrypt(sharedSecret);
        } catch (GeneralSecurityException e) {
            log.error("Failed to encrypt shared secret", e);
            encryptedSharedSecret = sharedSecret; // Fallback to plain (not recommended for production)
        }
        
        // Check for existing installation (reinstall case)
        Optional<BitbucketConnectInstallation> existingOpt = installationRepository.findByClientKey(clientKey);
        
        BitbucketConnectInstallation installation;
        if (existingOpt.isPresent()) {
            log.info("Updating existing installation for clientKey: {}", clientKey);
            installation = existingOpt.get();
        } else {
            log.info("Creating new installation for {}: {} ({})", principalType, tempSlug, workspaceUuid);
            installation = new BitbucketConnectInstallation();
            installation.setClientKey(clientKey);
        }
        
        // Update installation details
        installation.setSharedSecret(encryptedSharedSecret);
        installation.setBitbucketWorkspaceUuid(workspaceUuid);
        installation.setBitbucketWorkspaceSlug(tempSlug); // Temporary, will be updated
        installation.setBitbucketWorkspaceName(workspaceName);
        installation.setBaseApiUrl(baseApiUrl);
        installation.setInstalledByUuid(installedByUuid);
        installation.setInstalledByUsername(installedByUsername);
        installation.setOauthClientId(oauthClientId);
        installation.setPublicKey(publicKey);
        installation.setProductType(productType);
        installation.setEnabled(true);
        installation.setUpdatedAt(LocalDateTime.now());
        
        BitbucketConnectInstallation saved = installationRepository.save(installation);
        log.info("Saved Connect App installation: id={}, tempSlug={}", saved.getId(), tempSlug);
        
        // Fetch the real workspace slug from Bitbucket API
        try {
            fetchAndUpdateWorkspaceSlug(saved);
        } catch (Exception e) {
            log.warn("Failed to fetch real workspace slug, using temporary: {}", e.getMessage());
        }
        
        return saved;
    }
    
    /**
     * Fetch the real workspace slug from Bitbucket API and update the installation.
     * For personal accounts, the workspace slug is different from the username.
     */
    private void fetchAndUpdateWorkspaceSlug(BitbucketConnectInstallation installation) {
        try {
            TokenInfo tokenInfo = getAccessToken(installation);
            String accessToken = tokenInfo.accessToken();
            
            // Fetch workspaces the user/installation has access to
            Request request = new Request.Builder()
                    .url("https://api.bitbucket.org/2.0/workspaces?pagelen=100")
                    .addHeader("Authorization", "Bearer " + accessToken)
                    .get()
                    .build();
            
            try (Response response = httpClient.newCall(request).execute()) {
                if (!response.isSuccessful()) {
                    log.warn("Failed to fetch workspaces: {}", response.code());
                    return;
                }
                
                String responseBody = response.body() != null ? response.body().string() : "";
                JsonNode json = objectMapper.readTree(responseBody);
                JsonNode values = json.path("values");
                
                if (values.isArray() && values.size() > 0) {
                    // Find the workspace that matches our UUID
                    String targetUuid = installation.getBitbucketWorkspaceUuid();
                    for (JsonNode workspace : values) {
                        String uuid = workspace.path("uuid").asText();
                        if (uuid.equals(targetUuid) || uuid.equals("{" + targetUuid + "}") || 
                            targetUuid.equals("{" + uuid + "}")) {
                            String realSlug = workspace.path("slug").asText();
                            String realName = workspace.path("name").asText();
                            
                            if (!realSlug.isEmpty() && !realSlug.equals(installation.getBitbucketWorkspaceSlug())) {
                                log.info("Updating workspace slug from '{}' to '{}' (name: {})", 
                                        installation.getBitbucketWorkspaceSlug(), realSlug, realName);
                                installation.setBitbucketWorkspaceSlug(realSlug);
                                if (!realName.isEmpty()) {
                                    installation.setBitbucketWorkspaceName(realName);
                                }
                                installationRepository.save(installation);
                            }
                            return;
                        }
                    }
                    
                    // If we couldn't match by UUID, just use the first workspace
                    // This handles personal accounts where the UUID might be the user UUID
                    JsonNode firstWorkspace = values.get(0);
                    String realSlug = firstWorkspace.path("slug").asText();
                    String realName = firstWorkspace.path("name").asText();
                    
                    if (!realSlug.isEmpty() && !realSlug.equals(installation.getBitbucketWorkspaceSlug())) {
                        log.info("Using first workspace: slug='{}', name='{}' (personal account)", realSlug, realName);
                        installation.setBitbucketWorkspaceSlug(realSlug);
                        if (!realName.isEmpty()) {
                            installation.setBitbucketWorkspaceName(realName);
                        }
                        installationRepository.save(installation);
                    }
                }
            }
        } catch (Exception e) {
            log.warn("Failed to fetch workspace slug from Bitbucket API: {}", e.getMessage());
        }
    }
    
    /**
     * Handle the "uninstalled" lifecycle event from Bitbucket.
     */
    @Transactional
    public void handleUninstalled(String clientKey) {
        log.info("Processing Connect App uninstallation for clientKey: {}", clientKey);
        
        Optional<BitbucketConnectInstallation> installationOpt = installationRepository.findByClientKey(clientKey);
        if (installationOpt.isEmpty()) {
            log.warn("No installation found for clientKey: {}", clientKey);
            return;
        }
        
        BitbucketConnectInstallation installation = installationOpt.get();
        
        // If there's a linked VCS connection, update its status
        if (installation.getVcsConnection() != null) {
            VcsConnection conn = installation.getVcsConnection();
            conn.setSetupStatus(EVcsSetupStatus.DISABLED);
            vcsConnectionRepository.save(conn);
            log.info("Disabled VCS connection {} due to app uninstallation", conn.getId());
        }
        
        // Delete the installation record
        installationRepository.delete(installation);
        log.info("Deleted Connect App installation for workspace: {}", installation.getBitbucketWorkspaceSlug());
    }
    
    /**
     * Handle the "enabled" lifecycle event.
     */
    @Transactional
    public void handleEnabled(String clientKey) {
        log.info("Connect App enabled for clientKey: {}", clientKey);
        
        installationRepository.findByClientKey(clientKey).ifPresent(installation -> {
            installation.setEnabled(true);
            installation.setUpdatedAt(LocalDateTime.now());
            installationRepository.save(installation);
            
            // Re-enable linked VCS connection if exists
            if (installation.getVcsConnection() != null) {
                VcsConnection conn = installation.getVcsConnection();
                conn.setSetupStatus(EVcsSetupStatus.CONNECTED);
                vcsConnectionRepository.save(conn);
            }
        });
    }
    
    /**
     * Handle the "disabled" lifecycle event.
     */
    @Transactional
    public void handleDisabled(String clientKey) {
        log.info("Connect App disabled for clientKey: {}", clientKey);
        
        installationRepository.findByClientKey(clientKey).ifPresent(installation -> {
            installation.setEnabled(false);
            installation.setUpdatedAt(LocalDateTime.now());
            installationRepository.save(installation);
            
            // Disable linked VCS connection if exists
            if (installation.getVcsConnection() != null) {
                VcsConnection conn = installation.getVcsConnection();
                conn.setSetupStatus(EVcsSetupStatus.DISABLED);
                vcsConnectionRepository.save(conn);
            }
        });
    }
    
    /**
     * Verify a JWT token from Bitbucket Connect.
     * 
     * @param jwtToken The JWT token from the Authorization header
     * @return The verified JWT claims if valid, null otherwise
     */
    public Claims verifyJwt(String jwtToken) {
        try {
            // First, decode without verification to get the issuer (clientKey)
            // JWT format: header.payload.signature
            String[] parts = jwtToken.split("\\.");
            if (parts.length != 3) {
                log.warn("Invalid JWT format - expected 3 parts");
                return null;
            }
            
            // Decode payload to get issuer
            String payloadJson = new String(
                    java.util.Base64.getUrlDecoder().decode(parts[1]), 
                    StandardCharsets.UTF_8
            );
            JsonNode payloadNode = objectMapper.readTree(payloadJson);
            String clientKey = payloadNode.path("iss").asText(null);
            
            if (clientKey == null || clientKey.isBlank()) {
                log.warn("JWT token missing issuer");
                return null;
            }
            
            // Find the installation to get the shared secret
            Optional<BitbucketConnectInstallation> installationOpt = installationRepository.findByClientKey(clientKey);
            if (installationOpt.isEmpty()) {
                log.warn("No installation found for clientKey: {}", clientKey);
                return null;
            }
            
            BitbucketConnectInstallation installation = installationOpt.get();
            
            // Try to decrypt the shared secret. If it fails, it might be stored in plain text (legacy)
            String sharedSecret;
            try {
                sharedSecret = encryptionService.decrypt(installation.getSharedSecret());
            } catch (Exception e) {
                log.warn("Failed to decrypt shared secret, trying as plain text (legacy installation)");
                sharedSecret = installation.getSharedSecret();
            }
            
            // Create signing key from shared secret
            SecretKey key = Keys.hmacShaKeyFor(
                    sharedSecret.getBytes(StandardCharsets.UTF_8)
            );
            
            // Verify the token and parse claims
            Jws<Claims> claimsJws = Jwts.parserBuilder()
                    .setSigningKey(key)
                    .setAllowedClockSkewSeconds(60) // 60 seconds leeway for clock skew
                    .build()
                    .parseClaimsJws(jwtToken);
            
            Claims claims = claimsJws.getBody();
            
            // Verify issuer matches
            if (!clientKey.equals(claims.getIssuer())) {
                log.warn("JWT issuer mismatch");
                return null;
            }
            
            return claims;
            
        } catch (JwtException e) {
            log.warn("JWT verification failed: {}", e.getMessage());
            return null;
        } catch (Exception e) {
            log.error("Error verifying JWT: {}", e.getMessage(), e);
            return null;
        }
    }
    
    /**
     * Get the installation for a given client key.
     */
    public Optional<BitbucketConnectInstallation> getInstallation(String clientKey) {
        return installationRepository.findByClientKey(clientKey);
    }
    
    /**
     * Find installation by client key (alias for getInstallation).
     */
    public Optional<BitbucketConnectInstallation> findByClientKey(String clientKey) {
        return installationRepository.findByClientKey(clientKey);
    }
    
    /**
     * Get the installation for a Bitbucket workspace.
     */
    public Optional<BitbucketConnectInstallation> getInstallationByWorkspace(String workspaceSlug) {
        return installationRepository.findByBitbucketWorkspaceSlug(workspaceSlug);
    }
    
    /**
     * Link a Connect App installation to a CodeCrow workspace.
     * This creates a VCS connection that can be used for API access.
     */
    @Transactional
    public VcsConnectionDTO linkToCodecrowWorkspace(Long installationId, Long codecrowWorkspaceId) 
            throws GeneralSecurityException, IOException {
        
        BitbucketConnectInstallation installation = installationRepository.findById(installationId)
                .orElseThrow(() -> new IntegrationException("Installation not found"));
        
        Workspace workspace = workspaceRepository.findById(codecrowWorkspaceId)
                .orElseThrow(() -> new IntegrationException("Workspace not found"));
        
        // Check if already linked
        if (installation.getCodecrowWorkspace() != null 
                && installation.getCodecrowWorkspace().getId().equals(codecrowWorkspaceId)) {
            // Already linked, return existing connection
            if (installation.getVcsConnection() != null) {
                return VcsConnectionDTO.fromEntity(installation.getVcsConnection());
            }
        }
        
        // Get access token for the installation
        TokenInfo tokenInfo = getAccessToken(installation);
        
        // Create or update VCS connection
        VcsConnection connection = installation.getVcsConnection();
        if (connection == null) {
            connection = new VcsConnection();
            connection.setProviderType(EVcsProvider.BITBUCKET_CLOUD);
            connection.setConnectionType(EVcsConnectionType.APP);
        }
        
        connection.setWorkspace(workspace);
        connection.setConnectionName("Bitbucket – " + installation.getBitbucketWorkspaceName());
        connection.setExternalWorkspaceId(installation.getBitbucketWorkspaceUuid());
        connection.setExternalWorkspaceSlug(installation.getBitbucketWorkspaceSlug());
        connection.setAccessToken(encryptionService.encrypt(tokenInfo.accessToken));
        if (tokenInfo.refreshToken != null) {
            connection.setRefreshToken(encryptionService.encrypt(tokenInfo.refreshToken));
        }
        connection.setTokenExpiresAt(tokenInfo.expiresAt);
        connection.setSetupStatus(EVcsSetupStatus.CONNECTED);
        
        VcsConnection savedConnection = vcsConnectionRepository.save(connection);
        
        // Link installation to workspace and connection
        installation.setCodecrowWorkspace(workspace);
        installation.setVcsConnection(savedConnection);
        installation.setAccessToken(encryptionService.encrypt(tokenInfo.accessToken));
        if (tokenInfo.refreshToken != null) {
            installation.setRefreshToken(encryptionService.encrypt(tokenInfo.refreshToken));
        }
        installation.setTokenExpiresAt(tokenInfo.expiresAt);
        installationRepository.save(installation);
        
        // Get repo count
        try {
            org.rostilos.codecrow.vcsclient.VcsClient client = createClient(tokenInfo.accessToken);
            int repoCount = client.getRepositoryCount(installation.getBitbucketWorkspaceSlug());
            savedConnection.setRepoCount(repoCount);
            vcsConnectionRepository.save(savedConnection);
        } catch (Exception e) {
            log.warn("Failed to get repo count: {}", e.getMessage());
        }
        
        log.info("Linked Connect App installation {} to CodeCrow workspace {}", 
                installationId, codecrowWorkspaceId);
        
        return VcsConnectionDTO.fromEntity(savedConnection);
    }
    
    /**
     * Get an access token for API calls using JWT Bearer grant.
     * For Connect Apps, we use the installation's sharedSecret to create a JWT
     * and exchange it for an access token.
     */
    public TokenInfo getAccessToken(BitbucketConnectInstallation installation) throws IOException, GeneralSecurityException {
        // Check if we have a valid cached token
        if (installation.getAccessToken() != null && installation.getTokenExpiresAt() != null) {
            if (installation.getTokenExpiresAt().isAfter(LocalDateTime.now().plusMinutes(5))) {
                try {
                    String decryptedToken = encryptionService.decrypt(installation.getAccessToken());
                    return new TokenInfo(decryptedToken, null, installation.getTokenExpiresAt());
                } catch (Exception e) {
                    log.warn("Failed to decrypt cached token, fetching new one: {}", e.getMessage());
                }
            }
        }
        
        // For Connect Apps, we need to create a JWT and exchange it for an access token
        // using the "urn:bitbucket:oauth2:jwt" grant type
        String sharedSecret = installation.getSharedSecret();
        if (sharedSecret == null || sharedSecret.isBlank()) {
            throw new IOException("No shared secret available for installation: " + installation.getClientKey());
        }
        
        // Decrypt the shared secret
        String decryptedSecret;
        try {
            decryptedSecret = encryptionService.decrypt(sharedSecret);
            log.info("Successfully decrypted shared secret for installation: {}, secret length: {}", 
                    installation.getClientKey(), decryptedSecret.length());
        } catch (Exception e) {
            // Shared secret might be stored unencrypted (old installation) - use as-is
            log.warn("Could not decrypt shared secret, using as stored: {}, length: {}", 
                    e.getMessage(), sharedSecret.length());
            decryptedSecret = sharedSecret;
        }
        
        // Create JWT for authentication
        long now = System.currentTimeMillis() / 1000;
        SecretKey key = Keys.hmacShaKeyFor(decryptedSecret.getBytes(StandardCharsets.UTF_8));
        
        String jwt = Jwts.builder()
                .setIssuer(installation.getClientKey())
                .setSubject(installation.getClientKey())
                .setIssuedAt(new java.util.Date(now * 1000))
                .setExpiration(new java.util.Date((now + 180) * 1000)) // 3 minutes validity
                .signWith(key)
                .compact();
        
        log.info("Created JWT for token exchange, iss: {}, jwt length: {}", installation.getClientKey(), jwt.length());
        
        // Exchange JWT for access token using JWT Bearer grant
        // The JWT must be sent in the Authorization header as "JWT <token>"
        Request request = new Request.Builder()
                .url(BITBUCKET_TOKEN_URL)
                .addHeader("Authorization", "JWT " + jwt)
                .addHeader("Content-Type", "application/x-www-form-urlencoded")
                .post(RequestBody.create("grant_type=urn:bitbucket:oauth2:jwt", FORM_MEDIA_TYPE))
                .build();
        
        log.debug("Sending token request to: {}", BITBUCKET_TOKEN_URL);
        
        try (Response response = httpClient.newCall(request).execute()) {
            String responseBody = response.body() != null ? response.body().string() : "";
            
            if (!response.isSuccessful()) {
                log.error("Failed to get access token via JWT Bearer: {} - {}", response.code(), responseBody);
                throw new IOException("Failed to get access token: " + response.code() + " - " + responseBody);
            }
            
            JsonNode json = objectMapper.readTree(responseBody);
            String accessToken = json.path("access_token").asText();
            String refreshToken = json.has("refresh_token") ? json.path("refresh_token").asText() : null;
            int expiresIn = json.path("expires_in").asInt(3600);
            LocalDateTime expiresAt = LocalDateTime.now().plusSeconds(expiresIn);
            
            log.info("Successfully obtained access token for installation: {}", installation.getClientKey());
            
            return new TokenInfo(accessToken, refreshToken, expiresAt);
        }
    }
    
    /**
     * Get all unlinked installations (not yet linked to a CodeCrow workspace).
     * This is an internal/admin method - use getUnlinkedInstallationsForUser for user-facing endpoints.
     */
    public List<BitbucketConnectInstallation> getUnlinkedInstallations() {
        return installationRepository.findAll().stream()
                .filter(i -> i.getCodecrowWorkspace() == null)
                .toList();
    }
    
    /**
     * Get unlinked installations that a specific user can access.
     * 
     * Security model:
     * - Users can only see installations for Bitbucket workspaces that have existing
     *   VCS connections in CodeCrow workspaces they belong to.
     * - Matching is done by both slug AND UUID to handle personal accounts.
     * - If no matching connections, the installation won't be shown.
     */
    public List<BitbucketConnectInstallation> getUnlinkedInstallationsForUser(Long userId) {
        // Get user's CodeCrow workspaces
        List<Workspace> userWorkspaces = workspaceMemberRepository.findActiveWorkspacesByUserId(userId);
        
        if (userWorkspaces.isEmpty()) {
            log.debug("User {} has no workspaces, returning empty list", userId);
            return List.of();
        }
        
        // Get all Bitbucket workspace identifiers from VcsConnections in user's workspaces
        // Collect both slugs and UUIDs for matching
        List<VcsConnection> bitbucketConnections = userWorkspaces.stream()
                .flatMap(ws -> vcsConnectionRepository.findByWorkspace_Id(ws.getId()).stream())
                .filter(conn -> conn.getProviderType() == EVcsProvider.BITBUCKET_CLOUD)
                .toList();
        
        List<String> userBitbucketSlugs = bitbucketConnections.stream()
                .map(VcsConnection::getExternalWorkspaceSlug)
                .filter(slug -> slug != null && !slug.isBlank())
                .distinct()
                .toList();
        
        List<String> userBitbucketUuids = bitbucketConnections.stream()
                .map(VcsConnection::getExternalWorkspaceId)
                .filter(uuid -> uuid != null && !uuid.isBlank())
                .distinct()
                .toList();
        
        if (userBitbucketSlugs.isEmpty() && userBitbucketUuids.isEmpty()) {
            log.debug("User {} has no Bitbucket connections in any workspace, returning empty list", userId);
            return List.of();
        }
        
        log.debug("User {} has connections to Bitbucket workspaces - slugs: {}, uuids: {}", 
                userId, userBitbucketSlugs, userBitbucketUuids);
        
        // Get unlinked installations that match the user's Bitbucket workspaces (by slug OR uuid)
        return installationRepository.findAll().stream()
                .filter(i -> i.getCodecrowWorkspace() == null) // Only unlinked
                .filter(i -> userBitbucketSlugs.contains(i.getBitbucketWorkspaceSlug()) 
                        || userBitbucketUuids.contains(i.getBitbucketWorkspaceUuid()))
                .toList();
    }
    
    /**
     * Check if a user can access (view/link) a specific installation.
     * User must be a member of a CodeCrow workspace that has a VCS connection 
     * to the same Bitbucket workspace (matched by slug OR uuid).
     */
    public boolean canUserAccessInstallation(Long userId, Long installationId) {
        Optional<BitbucketConnectInstallation> installationOpt = installationRepository.findById(installationId);
        if (installationOpt.isEmpty()) {
            return false;
        }
        
        BitbucketConnectInstallation installation = installationOpt.get();
        String bitbucketWorkspaceSlug = installation.getBitbucketWorkspaceSlug();
        String bitbucketWorkspaceUuid = installation.getBitbucketWorkspaceUuid();
        
        // Get user's CodeCrow workspaces
        List<Workspace> userWorkspaces = workspaceMemberRepository.findActiveWorkspacesByUserId(userId);
        
        // Check if any of the user's workspaces have a connection to this Bitbucket workspace
        // Match by either slug OR uuid
        return userWorkspaces.stream()
                .flatMap(ws -> vcsConnectionRepository.findByWorkspace_Id(ws.getId()).stream())
                .filter(conn -> conn.getProviderType() == EVcsProvider.BITBUCKET_CLOUD)
                .anyMatch(conn -> {
                    boolean slugMatch = bitbucketWorkspaceSlug != null 
                            && bitbucketWorkspaceSlug.equals(conn.getExternalWorkspaceSlug());
                    boolean uuidMatch = bitbucketWorkspaceUuid != null 
                            && bitbucketWorkspaceUuid.equals(conn.getExternalWorkspaceId());
                    return slugMatch || uuidMatch;
                });
    }
    
    /**
     * Get all installations linked to a CodeCrow workspace.
     */
    public List<BitbucketConnectInstallation> getInstallationsForWorkspace(Long codecrowWorkspaceId) {
        return installationRepository.findByCodecrowWorkspace_Id(codecrowWorkspaceId);
    }
    
    /**
     * Check if Connect App is configured.
     */
    public boolean isConfigured() {
        return connectClientId != null && !connectClientId.isBlank()
                && connectClientSecret != null && !connectClientSecret.isBlank();
    }
    
    /**
     * Get the Connect App descriptor.
     * 
     * Note: Webhooks are NOT registered via Connect App. Instead, project-level webhooks
     * are created when a project is bound to a repository, pointing directly to the 
     * pipeline-agent with auth token in URL (/api/webhooks/bitbucket-cloud/{authToken}).
     * This approach is simpler, more secure (per-project auth), and doesn't require
     * JWT validation middleware.
     */
    public JsonNode getDescriptor() {
        try {
            // Load the descriptor template and replace placeholders
            // No webhooks - they are created per-project via VCS API
            String template = """
                {
                  "key": "codecrow-connect-app",
                  "name": "CodeCrow - AI Code Review",
                  "description": "AI-powered code review platform with RAG context.",
                  "vendor": {
                    "name": "CodeCrow",
                    "url": "%s"
                  },
                  "baseUrl": "%s",
                  "authentication": {
                    "type": "jwt"
                  },
                  "lifecycle": {
                    "installed": "/api/bitbucket/connect/installed",
                    "uninstalled": "/api/bitbucket/connect/uninstalled",
                    "enabled": "/api/bitbucket/connect/enabled",
                    "disabled": "/api/bitbucket/connect/disabled"
                  },
                  "scopes": [
                    "account",
                    "repository",
                    "repository:write",
                    "pullrequest",
                    "pullrequest:write",
                    "webhook"
                  ],
                  "contexts": ["account"],
                  "modules": {
                    "configurePage": {
                      "url": "/api/bitbucket/connect/configure?signed_request={signed_request}",
                      "key": "codecrow-configure",
                      "name": {"value": "Setup CodeCrow"}
                    }
                  }
                }
                """.formatted(baseFrontendUrl, baseUrl);
            
            return objectMapper.readTree(template);
        } catch (Exception e) {
            log.error("Failed to generate descriptor", e);
            throw new RuntimeException("Failed to generate Connect App descriptor", e);
        }
    }
    
    private org.rostilos.codecrow.vcsclient.VcsClient createClient(String accessToken) {
        OkHttpClient authClient = httpClient.newBuilder()
                .addInterceptor(chain -> {
                    Request original = chain.request();
                    Request request = original.newBuilder()
                            .header("Authorization", "Bearer " + accessToken)
                            .build();
                    return chain.proceed(request);
                })
                .build();
        
        return new org.rostilos.codecrow.vcsclient.bitbucket.cloud.BitbucketCloudClient(authClient);
    }
    
    /**
     * Token information holder.
     */
    public record TokenInfo(String accessToken, String refreshToken, LocalDateTime expiresAt) {}
}
