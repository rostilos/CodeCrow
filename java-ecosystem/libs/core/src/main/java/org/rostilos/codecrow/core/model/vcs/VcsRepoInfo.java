package org.rostilos.codecrow.core.model.vcs;

/**
 * Common interface for VCS repository information.
 * Implemented by both ProjectVcsConnectionBinding and VcsRepoBinding.
 */
public interface VcsRepoInfo {
    
    /**
     * Get the workspace/namespace of the repository in the external provider.
     * For Bitbucket: workspace slug
     * For GitHub: owner login
     * For GitLab: namespace path
     */
    String getRepoWorkspace();
    
    /**
     * Get the repository slug in the external provider.
     */
    String getRepoSlug();
    
    /**
     * Get the VCS connection used to access this repository.
     */
    VcsConnection getVcsConnection();
}
