package org.rostilos.codecrow.analysisengine.processor;

import org.rostilos.codecrow.core.model.vcs.VcsConnection;
import org.rostilos.codecrow.core.model.vcs.VcsRepoInfo;

public record VcsRepoInfoImpl(VcsConnection vcsConnection, String workspace, String repoSlug) implements VcsRepoInfo {
    @Override
    public String getRepoSlug() {
        return repoSlug;
    }

    public String getRepoWorkspace() {
        return workspace;
    }

    public VcsConnection getVcsConnection() {
        return vcsConnection;
    }
}