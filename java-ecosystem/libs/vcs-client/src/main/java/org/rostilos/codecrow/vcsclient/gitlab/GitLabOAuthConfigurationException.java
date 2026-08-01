package org.rostilos.codecrow.vcsclient.gitlab;

import org.rostilos.codecrow.vcsclient.VcsClientException;

/**
 * Raised when GitLab OAuth credentials cannot be safely paired with an issuer.
 */
public class GitLabOAuthConfigurationException extends VcsClientException {

    public GitLabOAuthConfigurationException(String message) {
        super(message);
    }

    public GitLabOAuthConfigurationException(String message, Throwable cause) {
        super(message, cause);
    }
}
