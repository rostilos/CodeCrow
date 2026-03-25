package org.rostilos.codecrow.taskmanagement;

import org.rostilos.codecrow.taskmanagement.jira.cloud.JiraCloudClient;
import org.rostilos.codecrow.taskmanagement.jira.cloud.JiraCloudConfig;
import org.slf4j.Logger;
import org.slf4j.LoggerFactory;
import org.springframework.stereotype.Component;

/**
 * Factory that creates {@link TaskManagementClient} instances for a given platform.
 * <p>
 * Currently supports:
 * <ul>
 *   <li>{@link ETaskManagementPlatform#JIRA_CLOUD} — fully implemented</li>
 *   <li>{@link ETaskManagementPlatform#JIRA_DATA_CENTER} — planned, throws {@link UnsupportedOperationException}</li>
 * </ul>
 * </p>
 */
@Component
public class TaskManagementClientFactory {

    private static final Logger log = LoggerFactory.getLogger(TaskManagementClientFactory.class);

    /**
     * Create a task management client for the specified platform.
     *
     * @param platform  the target platform
     * @param baseUrl   base URL of the platform instance (e.g. "https://myorg.atlassian.net")
     * @param email     user email for authentication (Jira Cloud uses email + API token)
     * @param apiToken  API token or personal access token
     * @return a configured client
     * @throws UnsupportedOperationException if the platform is not yet implemented
     */
    public TaskManagementClient createClient(ETaskManagementPlatform platform,
                                              String baseUrl,
                                              String email,
                                              String apiToken) {
        return switch (platform) {
            case JIRA_CLOUD -> {
                log.debug("Creating Jira Cloud client for {}", baseUrl);
                JiraCloudConfig config = new JiraCloudConfig(baseUrl, email, apiToken);
                yield new JiraCloudClient(config);
            }
            case JIRA_DATA_CENTER -> throw new UnsupportedOperationException(
                    "Jira Data Center support is coming soon. " +
                    "Please use Jira Cloud for now."
            );
        };
    }
}
