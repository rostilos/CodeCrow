package org.rostilos.codecrow.webserver.project.service;

import org.rostilos.codecrow.core.model.project.AllowedCommandUser;
import org.rostilos.codecrow.core.model.project.Project;
import org.rostilos.codecrow.core.model.vcs.EVcsProvider;
import org.rostilos.codecrow.core.model.vcs.VcsConnection;
import org.rostilos.codecrow.core.persistence.repository.project.AllowedCommandUserRepository;
import org.rostilos.codecrow.vcsclient.VcsClient;
import org.rostilos.codecrow.vcsclient.VcsClientProvider;
import org.rostilos.codecrow.vcsclient.model.VcsCollaborator;
import org.slf4j.Logger;
import org.slf4j.LoggerFactory;
import org.springframework.stereotype.Service;
import org.springframework.transaction.annotation.Transactional;

import java.time.OffsetDateTime;
import java.util.*;

/**
 * Service for managing allowed command users.
 * 
 * This service provides methods to:
 * - Add/remove users from the allowed list
 * - Enable/disable individual users
 * - Sync users from VCS collaborators
 * - Query allowed users
 */
@Service
public class AllowedCommandUserService {
    
    private static final Logger log = LoggerFactory.getLogger(AllowedCommandUserService.class);
    
    private final AllowedCommandUserRepository allowedUserRepository;
    private final VcsClientProvider vcsClientProvider;
    
    // Permission levels that grant command access (for REPO_WRITE_ACCESS mode)
    private static final Set<String> WRITE_PERMISSIONS = Set.of(
        "write", "admin", "maintain", "push", "owner"
    );
    
    public AllowedCommandUserService(
            AllowedCommandUserRepository allowedUserRepository,
            VcsClientProvider vcsClientProvider) {
        this.allowedUserRepository = allowedUserRepository;
        this.vcsClientProvider = vcsClientProvider;
    }
    
    /**
     * Get all allowed users for a project.
     */
    public List<AllowedCommandUser> getAllowedUsers(Long projectId) {
        return allowedUserRepository.findByProjectId(projectId);
    }
    
    /**
     * Get enabled allowed users for a project.
     */
    public List<AllowedCommandUser> getEnabledAllowedUsers(Long projectId) {
        return allowedUserRepository.findByProjectIdAndEnabledTrue(projectId);
    }
    
    /**
     * Count total allowed users for a project.
     */
    public long countAllowedUsers(Long projectId) {
        return allowedUserRepository.countByProjectId(projectId);
    }
    
    /**
     * Count enabled allowed users for a project.
     */
    public long countEnabledAllowedUsers(Long projectId) {
        return allowedUserRepository.countByProjectIdAndEnabledTrue(projectId);
    }
    
    /**
     * Add a user to the allowed list.
     */
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
    
    /**
     * Remove a user from the allowed list.
     */
    @Transactional
    public void removeAllowedUser(Long projectId, String vcsUserId) {
        allowedUserRepository.deleteByProjectIdAndVcsUserId(projectId, vcsUserId);
    }
    
    /**
     * Enable or disable a user.
     */
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
     * Clear all allowed users for a project.
     */
    @Transactional
    public void clearAllowedUsers(Long projectId) {
        allowedUserRepository.deleteByProjectId(projectId);
    }
    
    /**
     * Sync allowed users from VCS collaborators.
     * Fetches repository collaborators with write access and adds them to the allowed list.
     */
    @Transactional
    public SyncResult syncFromVcs(Project project, String initiatedBy) {
        VcsConnection connection = getVcsConnection(project);
        
        if (connection == null) {
            return new SyncResult(false, 0, 0, 0, "No VCS connection available");
        }
        
        try {
            List<VcsCollaborator> collaborators = fetchCollaborators(connection, project);
            
            // Disable all synced users first (so we can re-enable those that still exist)
            allowedUserRepository.disableSyncedByProjectId(project.getId());
            
            int added = 0;
            int updated = 0;
            
            for (VcsCollaborator collab : collaborators) {
                // Only sync users with write permissions
                if (!hasWritePermission(collab.permission())) {
                    continue;
                }
                
                // Skip users without valid identifiers
                if (collab.userId() == null) {
                    log.warn("Skipping collaborator with null userId: {}", collab.displayName());
                    continue;
                }
                
                // Use displayName or userId as fallback if username is null
                // Bitbucket deprecated usernames, some accounts only have account_id
                String effectiveUsername = collab.username() != null ? collab.username() 
                    : (collab.displayName() != null ? collab.displayName() : collab.userId());
                
                boolean exists = allowedUserRepository.existsByProjectIdAndVcsUserId(
                    project.getId(), collab.userId());
                
                addAllowedUser(project, collab.userId(), effectiveUsername,
                    collab.displayName(), collab.avatarUrl(), collab.permission(),
                    true, initiatedBy);
                
                if (exists) updated++;
                else added++;
            }
            
            log.info("Synced {} collaborators for project {} ({} added, {} updated)", 
                collaborators.size(), project.getId(), added, updated);
            
            return new SyncResult(true, added, updated, collaborators.size(), null);
            
        } catch (Exception e) {
            log.error("Failed to sync collaborators for project {}: {}", project.getId(), e.getMessage());
            return new SyncResult(false, 0, 0, 0, e.getMessage());
        }
    }
    
    /**
     * Fetch collaborators from VCS provider.
     */
    private List<VcsCollaborator> fetchCollaborators(VcsConnection connection, Project project) {
        EVcsProvider provider = connection.getProviderType();
        
        try {
            VcsClient client = vcsClientProvider.getClient(connection);
            String workspace = getWorkspaceSlug(project);
            String repoSlug = getRepoSlug(project);
            
            if (workspace == null || repoSlug == null) {
                log.warn("Cannot fetch collaborators: missing workspace ({}) or repo slug ({})", workspace, repoSlug);
                return Collections.emptyList();
            }
            
            log.info("Fetching collaborators for {}/{} from {}", workspace, repoSlug, provider);
            return client.getRepositoryCollaborators(workspace, repoSlug);
            
        } catch (UnsupportedOperationException e) {
            log.warn("Collaborator sync not supported for provider: {}", provider);
            return Collections.emptyList();
        } catch (Exception e) {
            log.error("Error fetching collaborators for provider {}: {}", provider, e.getMessage(), e);
            throw new RuntimeException("Failed to fetch collaborators: " + e.getMessage(), e);
        }
    }
    
    /**
     * Get the workspace slug from project bindings using unified accessor.
     */
    private String getWorkspaceSlug(Project project) {
        var vcsInfo = project.getEffectiveVcsRepoInfo();
        return vcsInfo != null ? vcsInfo.getRepoWorkspace() : null;
    }
    
    /**
     * Get the repository slug from project bindings using unified accessor.
     */
    private String getRepoSlug(Project project) {
        var vcsInfo = project.getEffectiveVcsRepoInfo();
        return vcsInfo != null ? vcsInfo.getRepoSlug() : null;
    }
    
    private boolean hasWritePermission(String permission) {
        return permission != null && WRITE_PERMISSIONS.contains(permission.toLowerCase());
    }
    
    /**
     * Get VCS connection from project using unified accessor.
     */
    private VcsConnection getVcsConnection(Project project) {
        return project.getEffectiveVcsConnection();
    }
    
    /**
     * Get VCS provider from project.
     */
    private EVcsProvider getVcsProvider(Project project) {
        VcsConnection conn = getVcsConnection(project);
        return conn != null ? conn.getProviderType() : null;
    }
    
    // ==================== Result Records ====================
    
    public record SyncResult(boolean success, int added, int updated, int totalFetched, String error) {}
}
