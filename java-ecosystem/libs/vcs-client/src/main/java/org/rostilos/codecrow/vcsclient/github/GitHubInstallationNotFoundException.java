package org.rostilos.codecrow.vcsclient.github;

import java.io.IOException;

/**
 * GitHub no longer recognizes an installation ID for the authenticated App.
 */
public class GitHubInstallationNotFoundException extends IOException {

    private final long installationId;

    public GitHubInstallationNotFoundException(long installationId) {
        super("GitHub App installation was not found: " + installationId);
        this.installationId = installationId;
    }

    public long getInstallationId() {
        return installationId;
    }
}
