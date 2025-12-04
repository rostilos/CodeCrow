package org.rostilos.codecrow.vcsclient.utils;

import org.rostilos.codecrow.core.model.codeanalysis.CodeAnalysis;
import org.rostilos.codecrow.core.model.codeanalysis.IssueSeverity;
import org.rostilos.codecrow.core.model.project.Project;
import org.slf4j.Logger;
import org.slf4j.LoggerFactory;

public class LinksGenerator {
    private static final Logger log = LoggerFactory.getLogger(LinksGenerator.class);

    /**
     * Creates URL for dashboard view of the project
     */
    public static String createDashboardUrl(String baseUrl, Project project) {
        try {
            return String.format("%s/dashboard/projects/%s",
                    baseUrl,
                    project.getNamespace()
            );
        } catch (Exception e) {
            log.warn("Error creating dashboard URL for project {}: {}", project.getId(), e.getMessage());
            return baseUrl + "/projects";
        }
    }

    public static String createIssueUrl(String baseUrl, Project project, Long issueId) {
        try {
            return String.format("%s/dashboard/projects/%s/issues/%d",
                    baseUrl,
                    project.getNamespace(),
                    issueId
            );
        } catch (Exception e) {
            log.warn("Error creating issue URL: {}", e.getMessage());
            return baseUrl;
        }
    }

    public static String createStatusUrl(String baseUrl, Project project, Long platformPrEntityId, int prVersion, String status) {
        try {
            return String.format("%s/dashboard/projects/%s?prId=%d&version=%d&status=%s",
                    baseUrl,
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
            return String.format("%s/dashboard/projects/%s?prId=%d&version=%d&severity=%s",
                    baseUrl,
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

    public static String createPlatformAnalysisUrl(String baseurl, CodeAnalysis analysis , Long platformPrEntityId) {
        try {
            Project project = analysis.getProject();
            if (project != null && project.getNamespace() != null && platformPrEntityId != null) {
                return String.format("%s/dashboard/projects/%s?prId=%s", baseurl, project.getNamespace(), platformPrEntityId);
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
}
