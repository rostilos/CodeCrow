package org.rostilos.codecrow.webserver.dto.response.integration;

/**
 * Response for onboarding a repository.
 */
public record RepoOnboardResponse(
    Long projectId,
    String projectName,
    String projectNamespace,
    VcsRepoBindingDTO binding,
    boolean webhooksConfigured,
    String message
) {
    /**
     * Create a success response.
     */
    public static RepoOnboardResponse success(Long projectId, String projectName, String namespace, 
                                               VcsRepoBindingDTO binding, boolean webhooksConfigured) {
        return new RepoOnboardResponse(
            projectId,
            projectName,
            namespace,
            binding,
            webhooksConfigured,
            "Repository successfully onboarded"
        );
    }
    
    /**
     * Create an error response.
     */
    public static RepoOnboardResponse error(String message) {
        return new RepoOnboardResponse(null, null, null, null, false, message);
    }
}
