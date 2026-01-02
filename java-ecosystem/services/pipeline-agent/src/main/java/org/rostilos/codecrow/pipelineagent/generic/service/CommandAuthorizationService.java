package org.rostilos.codecrow.pipelineagent.generic.service;

import org.rostilos.codecrow.core.model.project.AllowedCommandUser;
import org.rostilos.codecrow.core.model.project.Project;
import org.rostilos.codecrow.core.model.project.config.ProjectConfig;
import org.rostilos.codecrow.core.model.project.config.ProjectConfig.CommandAuthorizationMode;
import org.rostilos.codecrow.core.model.project.config.ProjectConfig.CommentCommandsConfig;
import org.rostilos.codecrow.core.model.vcs.EVcsProvider;
import org.rostilos.codecrow.core.persistence.repository.project.AllowedCommandUserRepository;
import org.rostilos.codecrow.pipelineagent.generic.webhook.WebhookPayload;
import org.slf4j.Logger;
import org.slf4j.LoggerFactory;
import org.springframework.stereotype.Service;
import org.springframework.transaction.annotation.Transactional;

import java.time.OffsetDateTime;
import java.util.*;

/**
 * Service for managing command authorization.
 * 
 * Handles:
 * - Checking if a user is authorized to execute commands
 * - Managing the allowed users list
 * 
 * Supports three authorization modes:
 * - ANYONE: Any user who can comment on a PR can execute commands
 * - ALLOWED_USERS_ONLY: Only users in the allowed list can execute commands
 * - PR_AUTHOR_ONLY: Only the PR author can execute commands
 */
@Service
public class CommandAuthorizationService {
    
    private static final Logger log = LoggerFactory.getLogger(CommandAuthorizationService.class);
    
    private final AllowedCommandUserRepository allowedUserRepository;
    
    public CommandAuthorizationService(AllowedCommandUserRepository allowedUserRepository) {
        this.allowedUserRepository = allowedUserRepository;
    }
    
    /**
     * Check if a user is authorized to execute commands for a project.
     * 
     * @param project The project
     * @param payload The webhook payload containing user info
     * @param prAuthorId PR author's VCS ID (from API enrichment)
     * @param prAuthorUsername PR author's username (from API enrichment)
     * @return Authorization result with details
     */
    public AuthorizationResult checkAuthorization(
            Project project, 
            WebhookPayload payload,
            String prAuthorId,
            String prAuthorUsername) {
        
        ProjectConfig config = project.getConfiguration();
        if (config == null) {
            return AuthorizationResult.denied("Project configuration not found");
        }
        
        CommentCommandsConfig commandsConfig = config.getCommentCommandsConfig();
        CommandAuthorizationMode mode = commandsConfig.getEffectiveAuthorizationMode();
        
        String vcsUserId = payload.commentData() != null ? payload.commentData().commentAuthorId() : null;
        String vcsUsername = payload.commentData() != null ? payload.commentData().commentAuthorUsername() : null;
        
        log.debug("Checking authorization: mode={}, vcsUserId={}, vcsUsername={}, prAuthorId={}", 
            mode, vcsUserId, vcsUsername, prAuthorId);
        
        // Check if PR author bypass is enabled (allowPrAuthor setting)
        if (commandsConfig.isPrAuthorAllowed() && mode != CommandAuthorizationMode.PR_AUTHOR_ONLY) {
            if (isPrAuthor(vcsUserId, vcsUsername, prAuthorId, prAuthorUsername)) {
                log.debug("User is PR author, authorized via PR author bypass");
                return AuthorizationResult.allowed("PR author");
            }
        }
        
        // Check based on authorization mode
        return switch (mode) {
            case ANYONE -> AuthorizationResult.allowed("ANYONE mode - all commenters allowed");
            
            case PR_AUTHOR_ONLY -> {
                if (isPrAuthor(vcsUserId, vcsUsername, prAuthorId, prAuthorUsername)) {
                    yield AuthorizationResult.allowed("PR author");
                }
                yield AuthorizationResult.denied("Only the PR author can execute commands on this project");
            }
            
            case ALLOWED_USERS_ONLY -> checkAllowedUsersList(project.getId(), vcsUserId, vcsUsername);
        };
    }
    
    /**
     * Simplified authorization check without PR author info.
     */
    public AuthorizationResult checkAuthorization(Project project, WebhookPayload payload) {
        return checkAuthorization(project, payload, null, null);
    }
    
    private boolean isPrAuthor(String vcsUserId, String vcsUsername, String prAuthorId, String prAuthorUsername) {
        if (vcsUserId != null && prAuthorId != null && vcsUserId.equals(prAuthorId)) {
            return true;
        }
        if (vcsUsername != null && prAuthorUsername != null && 
            vcsUsername.equalsIgnoreCase(prAuthorUsername)) {
            return true;
        }
        return false;
    }
    
    private AuthorizationResult checkAllowedUsersList(Long projectId, String vcsUserId, String vcsUsername) {
        if (vcsUserId != null && allowedUserRepository.existsByProjectIdAndVcsUserIdAndEnabledTrue(projectId, vcsUserId)) {
            log.debug("User {} found in allowed list by ID", vcsUserId);
            return AuthorizationResult.allowed("In allowed users list");
        }
        
        if (vcsUsername != null && allowedUserRepository.existsByProjectIdAndVcsUsernameAndEnabledTrue(projectId, vcsUsername)) {
            log.debug("User {} found in allowed list by username", vcsUsername);
            return AuthorizationResult.allowed("In allowed users list");
        }
        
        return AuthorizationResult.denied("You are not in the allowed users list for this project");
    }
    
    public List<AllowedCommandUser> getAllowedUsers(Long projectId) {
        return allowedUserRepository.findByProjectId(projectId);
    }
    
    public List<AllowedCommandUser> getEnabledAllowedUsers(Long projectId) {
        return allowedUserRepository.findByProjectIdAndEnabledTrue(projectId);
    }
    
    @Transactional
    public AllowedCommandUser addAllowedUser(
            Project project, 
            String vcsUserId, 
            String vcsUsername,
            String displayName,
            String avatarUrl,
            String repoPermission,
            boolean syncedFromVcs,
            String addedBy) {
        
        Optional<AllowedCommandUser> existing = allowedUserRepository
            .findByProjectIdAndVcsUserId(project.getId(), vcsUserId);
        
        if (existing.isPresent()) {
            AllowedCommandUser user = existing.get();
            user.setVcsUsername(vcsUsername);
            user.setDisplayName(displayName);
            user.setAvatarUrl(avatarUrl);
            user.setRepoPermission(repoPermission);
            user.setEnabled(true);
            if (syncedFromVcs) {
                user.setLastSyncedAt(OffsetDateTime.now());
            }
            return allowedUserRepository.save(user);
        }
        
        EVcsProvider provider = getVcsProvider(project);
        
        AllowedCommandUser user = new AllowedCommandUser(project, provider, vcsUserId, vcsUsername);
        user.setDisplayName(displayName);
        user.setAvatarUrl(avatarUrl);
        user.setRepoPermission(repoPermission);
        user.setSyncedFromVcs(syncedFromVcs);
        user.setAddedBy(addedBy);
        if (syncedFromVcs) {
            user.setLastSyncedAt(OffsetDateTime.now());
        }
        
        return allowedUserRepository.save(user);
    }
    
    @Transactional
    public void removeAllowedUser(Long projectId, String vcsUserId) {
        allowedUserRepository.deleteByProjectIdAndVcsUserId(projectId, vcsUserId);
    }
    
    @Transactional
    public AllowedCommandUser setUserEnabled(Long projectId, String vcsUserId, boolean enabled) {
        Optional<AllowedCommandUser> userOpt = allowedUserRepository
            .findByProjectIdAndVcsUserId(projectId, vcsUserId);
        
        if (userOpt.isEmpty()) {
            throw new IllegalArgumentException("User not found: " + vcsUserId);
        }
        
        AllowedCommandUser user = userOpt.get();
        user.setEnabled(enabled);
        return allowedUserRepository.save(user);
    }
    
    /**
     * Get the VCS provider for a project.
     */
    private EVcsProvider getVcsProvider(Project project) {
        if (project.getVcsBinding() != null) {
            if (project.getVcsBinding().getVcsProvider() != null) {
                return project.getVcsBinding().getVcsProvider();
            }
            if (project.getVcsBinding().getVcsConnection() != null) {
                return project.getVcsBinding().getVcsConnection().getProviderType();
            }
        }
        return null;
    }
    
    public record AuthorizationResult(boolean authorized, String reason) {
        public static AuthorizationResult allowed(String reason) {
            return new AuthorizationResult(true, reason);
        }
        public static AuthorizationResult denied(String reason) {
            return new AuthorizationResult(false, reason);
        }
    }
}
