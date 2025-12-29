package org.rostilos.codecrow.analysisengine.util;

import org.rostilos.codecrow.core.model.project.Project;
import org.rostilos.codecrow.core.model.vcs.EVcsProvider;
import org.rostilos.codecrow.core.model.vcs.VcsConnection;
import org.rostilos.codecrow.core.model.vcs.VcsRepoBinding;
import org.springframework.stereotype.Service;

@Service
public class VcsInfoHelper {
    public VcsInfo getVcsInfo(Project project) {
        if (project.getVcsBinding() != null) {
            return new VcsInfo(
                    project.getVcsBinding().getVcsConnection(),
                    project.getVcsBinding().getWorkspace(),
                    project.getVcsBinding().getRepoSlug()
            );
        }
        VcsRepoBinding repoBinding = project.getVcsRepoBinding();
        if (repoBinding != null && repoBinding.getVcsConnection() != null) {
            return new VcsInfo(
                    repoBinding.getVcsConnection(),
                    repoBinding.getExternalNamespace(),
                    repoBinding.getExternalRepoSlug()
            );
        }
        throw new IllegalStateException("No VCS connection configured for project: " + project.getId());
    }

    public EVcsProvider getVcsProvider(Project project) {
        VcsInfo vcsInfo = getVcsInfo(project);
        return vcsInfo.vcsConnection().getProviderType();
    }

    public record VcsInfo(VcsConnection vcsConnection, String workspace, String repoSlug) {}
}
