package org.rostilos.codecrow.analysisengine.util;

import org.rostilos.codecrow.analysisengine.processor.VcsRepoInfoImpl;
import org.rostilos.codecrow.core.model.project.Project;
import org.rostilos.codecrow.core.model.vcs.EVcsProvider;

public class ProjectVcsInfoRetriever {
    private ProjectVcsInfoRetriever() {
        // Utility class, prevent instantiation
    }

    public static VcsRepoInfoImpl getVcsInfo(Project project) {
        var vcsInfo = project.getEffectiveVcsRepoInfo();
        if (vcsInfo != null && vcsInfo.getVcsConnection() != null) {
            return new VcsRepoInfoImpl(
                    vcsInfo.getVcsConnection(),
                    vcsInfo.getRepoWorkspace(),
                    vcsInfo.getRepoSlug());
        }
        throw new IllegalStateException("No VCS connection configured for project: " + project.getId());
    }

    public static EVcsProvider getVcsProvider(Project project) {
        return getVcsInfo(project).getVcsConnection().getProviderType();
    }
}
