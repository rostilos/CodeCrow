package org.rostilos.codecrow.core.util;

import org.rostilos.codecrow.core.model.project.Project;
import org.rostilos.codecrow.core.model.vcs.EVcsProvider;
import org.rostilos.codecrow.core.model.vcs.VcsConnection;
import org.rostilos.codecrow.core.model.vcs.VcsRepoBinding;
import org.rostilos.codecrow.core.model.vcs.VcsRepoInfo;

/**
 * Utility class for working with VCS bindings in projects.
 * <p>
 * This helper provides methods to extract VCS information from a project,
 * handling both the new {@link VcsRepoBinding} and the legacy
 * {@link org.rostilos.codecrow.core.model.project.ProjectVcsConnectionBinding}.
 */
public final class VcsBindingHelper {

    private VcsBindingHelper() {
        // Utility class
    }

    /**
     * Gets the effective VCS repository info from a project.
     * Prefers VcsRepoBinding over the legacy ProjectVcsConnectionBinding.
     *
     * @param project the project to extract VCS info from
     * @return VcsRepoInfo if any binding exists, null otherwise
     */
    public static VcsRepoInfo getEffectiveVcsRepoInfo(Project project) {
        if (project == null) {
            return null;
        }
        return project.getEffectiveVcsRepoInfo();
    }

    /**
     * Gets the VCS connection from a project.
     *
     * @param project the project to extract connection from
     * @return VcsConnection if any binding exists, null otherwise
     */
    public static VcsConnection getVcsConnection(Project project) {
        if (project == null) {
            return null;
        }
        return project.getEffectiveVcsConnection();
    }

    /**
     * Gets the repository workspace/namespace from a project.
     *
     * @param project the project to extract workspace from
     * @return workspace string if any binding exists, null otherwise
     */
    public static String getRepoWorkspace(Project project) {
        VcsRepoInfo info = getEffectiveVcsRepoInfo(project);
        return info != null ? info.getRepoWorkspace() : null;
    }

    /**
     * Gets the repository slug from a project.
     *
     * @param project the project to extract slug from
     * @return repo slug if any binding exists, null otherwise
     */
    public static String getRepoSlug(Project project) {
        VcsRepoInfo info = getEffectiveVcsRepoInfo(project);
        return info != null ? info.getRepoSlug() : null;
    }

    /**
     * Gets the VCS provider from a project's VCS connection.
     *
     * @param project the project to extract provider from
     * @return EVcsProvider if connection exists, null otherwise
     */
    public static EVcsProvider getVcsProvider(Project project) {
        VcsConnection connection = getVcsConnection(project);
        return connection != null ? connection.getProviderType() : null;
    }

    /**
     * Checks if the project has a valid VCS binding with connection.
     *
     * @param project the project to check
     * @return true if project has a binding with a non-null VCS connection
     */
    public static boolean hasValidVcsBinding(Project project) {
        return getVcsConnection(project) != null;
    }

    /**
     * Gets the full repository path (workspace/slug) from a project.
     *
     * @param project the project to extract path from
     * @return full repo path in format "workspace/slug", or null if unavailable
     */
    public static String getFullRepoPath(Project project) {
        String workspace = getRepoWorkspace(project);
        String slug = getRepoSlug(project);
        if (workspace != null && slug != null) {
            return workspace + "/" + slug;
        }
        return null;
    }
}
