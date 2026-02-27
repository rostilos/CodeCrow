package org.rostilos.codecrow.core.model.pullrequest;

/**
 * State of a pull request as tracked by Codecrow.
 * Synced from VCS webhooks.
 */
public enum PullRequestState {
    OPEN,
    MERGED,
    DECLINED
}
