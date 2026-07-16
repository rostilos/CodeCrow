package org.rostilos.codecrow.analysisengine.service.vcs;

/**
 * Admission hook invoked after a provider has confirmed that the accepted
 * webhook head is still the pull request's live head, but before expensive
 * exact diff and file acquisition begins.
 *
 * <p>The hook may throw a runtime exception to stop acquisition when a newer
 * durable generation has already superseded the verified head.</p>
 */
@FunctionalInterface
public interface ExactHeadAdmission {
    void admit(String verifiedHeadRevision);
}
