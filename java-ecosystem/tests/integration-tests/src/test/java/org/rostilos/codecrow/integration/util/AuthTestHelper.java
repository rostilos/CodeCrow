package org.rostilos.codecrow.integration.util;

import org.rostilos.codecrow.core.model.ai.AIConnection;
import org.rostilos.codecrow.core.model.ai.AIProviderKey;
import org.rostilos.codecrow.core.model.user.User;
import org.rostilos.codecrow.core.model.user.status.EStatus;
import org.rostilos.codecrow.core.model.vcs.EVcsConnectionType;
import org.rostilos.codecrow.core.model.vcs.EVcsProvider;
import org.rostilos.codecrow.core.model.vcs.EVcsSetupStatus;
import org.rostilos.codecrow.core.model.vcs.VcsConnection;
import org.rostilos.codecrow.core.model.vcs.config.cloud.BitbucketCloudConfig;
import org.rostilos.codecrow.core.model.workspace.EWorkspaceRole;
import org.rostilos.codecrow.core.model.workspace.EMembershipStatus;
import org.rostilos.codecrow.core.model.workspace.Workspace;
import org.rostilos.codecrow.core.model.workspace.WorkspaceMember;
import org.rostilos.codecrow.core.persistence.repository.ai.AiConnectionRepository;
import org.rostilos.codecrow.core.persistence.repository.user.UserRepository;
import org.rostilos.codecrow.core.persistence.repository.vcs.VcsConnectionRepository;
import org.rostilos.codecrow.core.persistence.repository.workspace.WorkspaceMemberRepository;
import org.rostilos.codecrow.core.persistence.repository.workspace.WorkspaceRepository;
import org.rostilos.codecrow.security.jwt.utils.JwtUtils;
import org.springframework.beans.factory.annotation.Autowired;
import org.springframework.security.authentication.AuthenticationManager;
import org.springframework.security.authentication.UsernamePasswordAuthenticationToken;
import org.springframework.security.core.Authentication;
import org.springframework.security.crypto.password.PasswordEncoder;
import org.springframework.stereotype.Component;
import org.springframework.transaction.annotation.Transactional;

import java.time.LocalDateTime;
import java.util.Optional;
import java.util.UUID;

/**
 * Helper class for authentication-related test operations.
 * Provides utilities for creating test users, generating tokens, and managing auth state.
 */
@Component
public class AuthTestHelper {

    private static final String DEFAULT_ADMIN_EMAIL = "admin@codecrow-test.com";
    private static final String DEFAULT_ADMIN_PASSWORD = "AdminPassword123!";
    private static final String DEFAULT_USER_EMAIL = "user@codecrow-test.com";
    private static final String DEFAULT_USER_PASSWORD = "UserPassword123!";

    @Autowired
    private UserRepository userRepository;

    @Autowired
    private WorkspaceRepository workspaceRepository;

    @Autowired
    private WorkspaceMemberRepository memberRepository;

    @Autowired
    private VcsConnectionRepository vcsConnectionRepository;

    @Autowired
    private AiConnectionRepository aiConnectionRepository;

    @Autowired
    private PasswordEncoder passwordEncoder;

    @Autowired
    private AuthenticationManager authenticationManager;

    @Autowired
    private JwtUtils jwtUtils;

    private String adminToken;
    private String userToken;
    private User adminUser;
    private User regularUser;
    private Workspace defaultWorkspace;

    @Transactional
    public void initializeTestUsers() {
        adminUser = getOrCreateUser(DEFAULT_ADMIN_EMAIL, DEFAULT_ADMIN_PASSWORD);
        regularUser = getOrCreateUser(DEFAULT_USER_EMAIL, DEFAULT_USER_PASSWORD);
        defaultWorkspace = getOrCreateDefaultWorkspace();
        addUserToWorkspace(adminUser, defaultWorkspace, EWorkspaceRole.OWNER);
        addUserToWorkspace(regularUser, defaultWorkspace, EWorkspaceRole.MEMBER);
    }

    public String getAdminToken() {
        if (adminToken == null) {
            initializeTestUsers();
            adminToken = loginAndGetToken(DEFAULT_ADMIN_EMAIL, DEFAULT_ADMIN_PASSWORD);
        }
        return adminToken;
    }

    public String getUserToken() {
        if (userToken == null) {
            initializeTestUsers();
            userToken = loginAndGetToken(DEFAULT_USER_EMAIL, DEFAULT_USER_PASSWORD);
        }
        return userToken;
    }

    public User getAdminUser() {
        if (adminUser == null) {
            initializeTestUsers();
        }
        return adminUser;
    }

    public User getRegularUser() {
        if (regularUser == null) {
            initializeTestUsers();
        }
        return regularUser;
    }

    public Workspace getDefaultWorkspace() {
        if (defaultWorkspace == null) {
            initializeTestUsers();
        }
        return defaultWorkspace;
    }

    public Long getAdminUserId() {
        return getAdminUser().getId();
    }

    public Long getRegularUserId() {
        return getRegularUser().getId();
    }

    public String loginAndGetToken(String username, String password) {
        try {
            Authentication authentication = authenticationManager.authenticate(
                    new UsernamePasswordAuthenticationToken(username, password)
            );
            return jwtUtils.generateJwtToken(authentication);
        } catch (Exception e) {
            throw new RuntimeException("Failed to authenticate user: " + username, e);
        }
    }

    @Transactional
    public User getOrCreateUser(String email, String password) {
        Optional<User> existing = userRepository.findByUsername(email);
        if (existing.isPresent()) {
            return existing.get();
        }

        User user = new User(email, email, passwordEncoder.encode(password), "Test Company");
        user.setStatus(EStatus.STATUS_ACTIVE);

        return userRepository.save(user);
    }

    @Transactional
    public Workspace getOrCreateDefaultWorkspace() {
        String slug = "test-workspace";
        
        Optional<Workspace> existing = workspaceRepository.findBySlug(slug);
        if (existing.isPresent()) {
            return existing.get();
        }

        Workspace workspace = new Workspace();
        workspace.setName("Test Workspace");
        workspace.setSlug(slug);
        workspace.setDescription("Test workspace for integration tests");
        
        return workspaceRepository.save(workspace);
    }

    @Transactional
    public void addUserToWorkspace(User user, Workspace workspace, EWorkspaceRole role) {
        Optional<WorkspaceMember> existing = memberRepository
                .findByWorkspaceIdAndUserId(workspace.getId(), user.getId());
        
        if (existing.isPresent()) {
            WorkspaceMember member = existing.get();
            member.setRole(role);
            memberRepository.save(member);
        } else {
            WorkspaceMember member = new WorkspaceMember(workspace, user, role, EMembershipStatus.ACTIVE);
            memberRepository.save(member);
        }
    }

    /**
     * Creates a new workspace with the specified name and adds the owner as OWNER role.
     */
    @Transactional
    public Workspace createTestWorkspace(String name, User owner) {
        String slug = name.toLowerCase().replaceAll("[^a-z0-9]", "-") + "-" + System.currentTimeMillis();
        
        Workspace workspace = new Workspace();
        workspace.setName(name);
        workspace.setSlug(slug);
        workspace.setDescription("Test workspace for " + name);
        
        Workspace savedWorkspace = workspaceRepository.save(workspace);
        addUserToWorkspace(owner, savedWorkspace, EWorkspaceRole.OWNER);
        
        return savedWorkspace;
    }

    /**
     * Creates a test VCS connection directly in the database, bypassing OAuth validation.
     * This is useful for integration tests that need a VCS connection without real OAuth credentials.
     */
    @Transactional
    public VcsConnection createTestVcsConnection(Workspace workspace, String connectionName) {
        VcsConnection vcsConnection = new VcsConnection();
        vcsConnection.setWorkspace(workspace);
        vcsConnection.setConnectionName(connectionName);
        vcsConnection.setProviderType(EVcsProvider.BITBUCKET_CLOUD);
        vcsConnection.setConnectionType(EVcsConnectionType.OAUTH_MANUAL);
        vcsConnection.setSetupStatus(EVcsSetupStatus.CONNECTED);
        vcsConnection.setExternalWorkspaceId("test-workspace-" + UUID.randomUUID().toString().substring(0, 8));
        vcsConnection.setExternalWorkspaceSlug("test-workspace-slug");
        vcsConnection.setAccessToken("test-access-token-encrypted");
        vcsConnection.setRefreshToken("test-refresh-token-encrypted");
        vcsConnection.setTokenExpiresAt(LocalDateTime.now().plusHours(1));
        vcsConnection.setScopes("repository:read,repository:write");
        vcsConnection.setRepoCount(5);
        
        // Set up minimal configuration
        BitbucketCloudConfig config = new BitbucketCloudConfig(
                "test-oauth-key",
                "test-oauth-secret",
                vcsConnection.getExternalWorkspaceId()
        );
        vcsConnection.setConfiguration(config);
        
        return vcsConnectionRepository.save(vcsConnection);
    }

    /**
     * Creates a test VCS connection with specific provider type.
     */
    @Transactional
    public VcsConnection createTestVcsConnection(Workspace workspace, String connectionName, EVcsProvider provider) {
        VcsConnection vcsConnection = createTestVcsConnection(workspace, connectionName);
        vcsConnection.setProviderType(provider);
        return vcsConnectionRepository.save(vcsConnection);
    }

    /**
     * Creates a test AI connection directly in the database.
     * This is useful for integration tests that need an AI connection without real API keys.
     */
    @Transactional
    public AIConnection createTestAiConnection(Workspace workspace, String name, AIProviderKey provider) {
        AIConnection aiConnection = new AIConnection();
        aiConnection.setWorkspace(workspace);
        aiConnection.setName(name);
        aiConnection.setProviderKey(provider);
        aiConnection.setAiModel("gpt-4");
        aiConnection.setApiKeyEncrypted("test-encrypted-api-key-" + UUID.randomUUID().toString().substring(0, 8));
        
        return aiConnectionRepository.save(aiConnection);
    }

    /**
     * Creates a test AI connection with OpenAI provider.
     */
    @Transactional
    public AIConnection createTestAiConnection(Workspace workspace, String name) {
        return createTestAiConnection(workspace, name, AIProviderKey.OPENAI);
    }

    /**
     * Gets the VCS connection repository for test cleanup operations.
     */
    public VcsConnectionRepository getVcsConnectionRepository() {
        return vcsConnectionRepository;
    }

    /**
     * Gets the AI connection repository for test cleanup operations.
     */
    public AiConnectionRepository getAiConnectionRepository() {
        return aiConnectionRepository;
    }

    public void clearCachedTokens() {
        adminToken = null;
        userToken = null;
    }
}
