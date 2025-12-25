package org.rostilos.codecrow.vcsclient.utils;

import org.rostilos.codecrow.core.model.codeanalysis.CodeAnalysis;
import org.rostilos.codecrow.core.model.codeanalysis.IssueSeverity;
import org.rostilos.codecrow.core.model.project.Project;
import org.rostilos.codecrow.core.model.workspace.Workspace;
import org.slf4j.Logger;
import org.slf4j.LoggerFactory;

public class LinksGenerator {
    private static final Logger log = LoggerFactory.getLogger(LinksGenerator.class);

    /**
     * Creates URL for dashboard view of the project
     */
    public static String createDashboardUrl(String baseUrl, Project project) {
        try {
            String workspaceSlug = getWorkspaceSlug(project);
            return String.format("%s/dashboard/%s/projects/%s",
                    baseUrl,
                    workspaceSlug,
                    project.getNamespace()
            );
        } catch (Exception e) {
            log.warn("Error creating dashboard URL for project {}: {}", project.getId(), e.getMessage());
            return baseUrl + "/workspace";
        }
    }

    public static String createIssueUrl(String baseUrl, Project project, Long issueId) {
        try {
            String workspaceSlug = getWorkspaceSlug(project);
            return String.format("%s/dashboard/%s/projects/%s/issues/%d",
                    baseUrl,
                    workspaceSlug,
                    project.getNamespace(),
                    issueId
            );
        } catch (Exception e) {
            log.warn("Error creating issue URL: {}", e.getMessage());
            return baseUrl;
        }
    }

    public static String createBranchIssuesUrl(String baseUrl, Project project, String branchName) {
        try {
            String workspaceSlug = getWorkspaceSlug(project);
            return String.format("%s/dashboard/%s/projects/%s/branches/%s/issues",
                    baseUrl,
                    workspaceSlug,
                    project.getNamespace(),
                    encodePathSegment(branchName)
            );
        } catch (Exception e) {
            log.warn("Error creating branch issues URL: {}", e.getMessage());
            return baseUrl;
        }
    }

    public static String createBranchIssuesUrlWithSeverity(String baseUrl, Project project, String branchName, IssueSeverity severity) {
        try {
            String workspaceSlug = getWorkspaceSlug(project);
            return String.format("%s/dashboard/%s/projects/%s/branches/%s/issues?severity=%s",
                    baseUrl,
                    workspaceSlug,
                    project.getNamespace(),
                    encodePathSegment(branchName),
                    severity.name()
            );
        } catch (Exception e) {
            log.warn("Error creating branch issues URL with severity: {}", e.getMessage());
            return baseUrl;
        }
    }

    public static String createBranchIssuesUrlWithStatus(String baseUrl, Project project, String branchName, String status) {
        try {
            String workspaceSlug = getWorkspaceSlug(project);
            return String.format("%s/dashboard/%s/projects/%s/branches/%s/issues?status=%s",
                    baseUrl,
                    workspaceSlug,
                    project.getNamespace(),
                    encodePathSegment(branchName),
                    status
            );
        } catch (Exception e) {
            log.warn("Error creating branch issues URL with status: {}", e.getMessage());
            return baseUrl;
        }
    }

    public static String createStatusUrl(String baseUrl, Project project, Long platformPrEntityId, int prVersion, String status) {
        try {
            String workspaceSlug = getWorkspaceSlug(project);
            return String.format("%s/dashboard/%s/projects/%s?prId=%d&version=%d&status=%s",
                    baseUrl,
                    workspaceSlug,
                    project.getNamespace(),
                    platformPrEntityId,
                    prVersion,
                    status
            );
        } catch (Exception e) {
            log.warn("Error creating status URL: {}", e.getMessage());
            return baseUrl;
        }
    }

    /**
     * Creates URL for viewing issues of a specific severity
     */
    public static String createSeverityUrl(String baseUrl, Project project, Long platformPrEntityId, IssueSeverity severity, int prVersion) {
        try {
            String workspaceSlug = getWorkspaceSlug(project);
            return String.format("%s/dashboard/%s/projects/%s?prId=%d&version=%d&severity=%s",
                    baseUrl,
                    workspaceSlug,
                    project.getNamespace(),
                    platformPrEntityId,
                    prVersion,
                    severity.name()
            );
        } catch (Exception e) {
            log.warn("Error creating severity URL: {}", e.getMessage());
            return baseUrl;
        }
    }

    /**
     * Creates URL for the pull request (if available)
     */
    public static String createPullRequestUrl(CodeAnalysis analysis) {
        try {
            if (analysis.getPrNumber() != null) {
                // This is just a placeholder URL - the actual PR URL should be constructed
                // using the VCS provider's URL format
                return String.format("https://bitbucket.org/pull-requests/%s", analysis.getPrNumber());
            }
            return null;
        } catch (Exception e) {
            log.warn("Error creating pull request URL for analysis {}: {}", analysis.getId(), e.getMessage());
            return null;
        }
    }

    public static String createPlatformAnalysisUrl(String baseurl, CodeAnalysis analysis, Long platformPrEntityId) {
        try {
            Project project = analysis.getProject();
            if (project != null && project.getNamespace() != null && platformPrEntityId != null) {
                String workspaceSlug = getWorkspaceSlug(project);
                return String.format("%s/dashboard/%s/projects/%s?prId=%s", baseurl, workspaceSlug, project.getNamespace(), platformPrEntityId);
            }
            return null;
        } catch (Exception e) {
            log.warn("Error creating platform analysis URL for analysis {}: {}", analysis.getId(), e.getMessage());
            return null;
        }
    }

    public static String createMediaFileUrl(String baseUrl, String path) {
        return String.format("%s/%s", baseUrl, path);
    }

    /**
     * Get workspace slug from project, with fallback to "default" if not available
     */
    private static String getWorkspaceSlug(Project project) {
        Workspace workspace = project.getWorkspace();
        if (workspace != null && workspace.getSlug() != null) {
            return workspace.getSlug();
        }
        log.warn("Project {} has no workspace, using 'default' as fallback", project.getId());
        return "default";
    }

    /**
     * Encode path segment for URL (handles special characters in branch names)
     */
    private static String encodePathSegment(String segment) {
        try {
            return java.net.URLEncoder.encode(segment, "UTF-8").replace("+", "%20");
        } catch (Exception e) {
            return segment;
        }
    }
}
