package org.rostilos.codecrow.core.model.vcs;

import jakarta.persistence.*;
import java.time.LocalDateTime;

/**
 * Entity to store Bitbucket Connect App installations.
 * Each installation represents a workspace that has installed the CodeCrow app.
 */
@Entity
@Table(name = "bitbucket_connect_installation",
       indexes = {
           @Index(name = "idx_bci_client_key", columnList = "clientKey", unique = true),
           @Index(name = "idx_bci_workspace_uuid", columnList = "bitbucketWorkspaceUuid")
       })
public class BitbucketConnectInstallation {
    
    @Id
    @GeneratedValue(strategy = GenerationType.IDENTITY)
    private Long id;
    
    /**
     * Unique identifier for the installation provided by Bitbucket.
     * This is the primary key for identifying installations.
     */
    @Column(nullable = false, unique = true)
    private String clientKey;
    
    /**
     * Shared secret for JWT token verification.
     * This is provided during the installation callback.
     */
    @Column(nullable = false, length = 512)
    private String sharedSecret;
    
    /**
     * The Bitbucket workspace UUID where the app is installed.
     */
    @Column(nullable = false)
    private String bitbucketWorkspaceUuid;
    
    /**
     * The Bitbucket workspace slug (human-readable identifier).
     */
    @Column(nullable = false)
    private String bitbucketWorkspaceSlug;
    
    /**
     * The Bitbucket workspace display name.
     */
    private String bitbucketWorkspaceName;
    
    /**
     * The user who installed the app (principal).
     */
    private String installedByUuid;
    
    /**
     * Username of the user who installed the app.
     */
    private String installedByUsername;
    
    /**
     * The base URL for API calls (from the installation payload).
     */
    @Column(nullable = false)
    private String baseApiUrl;
    
    /**
     * Reference to the CodeCrow workspace this installation is linked to.
     * This is set when the user links the installation to their CodeCrow workspace.
     */
    @ManyToOne(fetch = FetchType.LAZY)
    @JoinColumn(name = "codecrow_workspace_id")
    private org.rostilos.codecrow.core.model.workspace.Workspace codecrowWorkspace;
    
    /**
     * Reference to the VCS connection created for this installation.
     */
    @OneToOne(fetch = FetchType.LAZY)
    @JoinColumn(name = "vcs_connection_id")
    private VcsConnection vcsConnection;
    
    /**
     * Whether the installation is currently enabled.
     */
    @Column(nullable = false)
    private boolean enabled = true;
    
    /**
     * Installation timestamp.
     */
    @Column(nullable = false)
    private LocalDateTime installedAt;
    
    /**
     * Last updated timestamp.
     */
    private LocalDateTime updatedAt;
    
    /**
     * Product type (bitbucket).
     */
    private String productType;
    
    /**
     * The public key for verifying JWT tokens (if using asymmetric verification).
     */
    @Column(length = 2048)
    private String publicKey;
    
    /**
     * OAuth client ID for API access token requests.
     */
    private String oauthClientId;
    
    /**
     * Encrypted OAuth access token for API calls.
     */
    @Column(length = 1024)
    private String accessToken;
    
    /**
     * Encrypted OAuth refresh token.
     */
    @Column(length = 1024)
    private String refreshToken;
    
    /**
     * Token expiration time.
     */
    private LocalDateTime tokenExpiresAt;
    
    // Constructors
    
    public BitbucketConnectInstallation() {
        this.installedAt = LocalDateTime.now();
    }
    
    // Getters and Setters
    
    public Long getId() {
        return id;
    }
    
    public void setId(Long id) {
        this.id = id;
    }
    
    public String getClientKey() {
        return clientKey;
    }
    
    public void setClientKey(String clientKey) {
        this.clientKey = clientKey;
    }
    
    public String getSharedSecret() {
        return sharedSecret;
    }
    
    public void setSharedSecret(String sharedSecret) {
        this.sharedSecret = sharedSecret;
    }
    
    public String getBitbucketWorkspaceUuid() {
        return bitbucketWorkspaceUuid;
    }
    
    public void setBitbucketWorkspaceUuid(String bitbucketWorkspaceUuid) {
        this.bitbucketWorkspaceUuid = bitbucketWorkspaceUuid;
    }
    
    public String getBitbucketWorkspaceSlug() {
        return bitbucketWorkspaceSlug;
    }
    
    public void setBitbucketWorkspaceSlug(String bitbucketWorkspaceSlug) {
        this.bitbucketWorkspaceSlug = bitbucketWorkspaceSlug;
    }
    
    public String getBitbucketWorkspaceName() {
        return bitbucketWorkspaceName;
    }
    
    public void setBitbucketWorkspaceName(String bitbucketWorkspaceName) {
        this.bitbucketWorkspaceName = bitbucketWorkspaceName;
    }
    
    public String getInstalledByUuid() {
        return installedByUuid;
    }
    
    public void setInstalledByUuid(String installedByUuid) {
        this.installedByUuid = installedByUuid;
    }
    
    public String getInstalledByUsername() {
        return installedByUsername;
    }
    
    public void setInstalledByUsername(String installedByUsername) {
        this.installedByUsername = installedByUsername;
    }
    
    public String getBaseApiUrl() {
        return baseApiUrl;
    }
    
    public void setBaseApiUrl(String baseApiUrl) {
        this.baseApiUrl = baseApiUrl;
    }
    
    public org.rostilos.codecrow.core.model.workspace.Workspace getCodecrowWorkspace() {
        return codecrowWorkspace;
    }
    
    public void setCodecrowWorkspace(org.rostilos.codecrow.core.model.workspace.Workspace codecrowWorkspace) {
        this.codecrowWorkspace = codecrowWorkspace;
    }
    
    public VcsConnection getVcsConnection() {
        return vcsConnection;
    }
    
    public void setVcsConnection(VcsConnection vcsConnection) {
        this.vcsConnection = vcsConnection;
    }
    
    public boolean isEnabled() {
        return enabled;
    }
    
    public void setEnabled(boolean enabled) {
        this.enabled = enabled;
    }
    
    public LocalDateTime getInstalledAt() {
        return installedAt;
    }
    
    public void setInstalledAt(LocalDateTime installedAt) {
        this.installedAt = installedAt;
    }
    
    public LocalDateTime getUpdatedAt() {
        return updatedAt;
    }
    
    public void setUpdatedAt(LocalDateTime updatedAt) {
        this.updatedAt = updatedAt;
    }
    
    public String getProductType() {
        return productType;
    }
    
    public void setProductType(String productType) {
        this.productType = productType;
    }
    
    public String getPublicKey() {
        return publicKey;
    }
    
    public void setPublicKey(String publicKey) {
        this.publicKey = publicKey;
    }
    
    public String getOauthClientId() {
        return oauthClientId;
    }
    
    public void setOauthClientId(String oauthClientId) {
        this.oauthClientId = oauthClientId;
    }
    
    public String getAccessToken() {
        return accessToken;
    }
    
    public void setAccessToken(String accessToken) {
        this.accessToken = accessToken;
    }
    
    public String getRefreshToken() {
        return refreshToken;
    }
    
    public void setRefreshToken(String refreshToken) {
        this.refreshToken = refreshToken;
    }
    
    public LocalDateTime getTokenExpiresAt() {
        return tokenExpiresAt;
    }
    
    public void setTokenExpiresAt(LocalDateTime tokenExpiresAt) {
        this.tokenExpiresAt = tokenExpiresAt;
    }
    
    @PreUpdate
    protected void onUpdate() {
        this.updatedAt = LocalDateTime.now();
    }
}
