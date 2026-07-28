package org.rostilos.codecrow.webserver.exception;

import java.io.IOException;

/**
 * Signals that a stale GitHub installation was converted into a safe,
 * connection-bound continuation URL.
 *
 * <p>This is checked so the surrounding transaction commits the recovered
 * PENDING or CONNECTED connection state before the controller redirects.</p>
 */
public class GitHubInstallationRecoveryException extends IOException {

    private final String redirectUrl;

    public GitHubInstallationRecoveryException(String redirectUrl, Throwable cause) {
        super("GitHub App installation recovery requires a redirect", cause);
        this.redirectUrl = redirectUrl;
    }

    public String getRedirectUrl() {
        return redirectUrl;
    }
}
