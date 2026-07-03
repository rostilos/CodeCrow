package org.rostilos.codecrow.pipelineagent.generic.service;

import org.rostilos.codecrow.core.model.project.Project;
import org.rostilos.codecrow.core.model.project.config.QaAutoDocConfig;
import org.rostilos.codecrow.core.model.taskmanagement.ETaskManagementConnectionStatus;
import org.rostilos.codecrow.core.model.taskmanagement.TaskManagementConnection;
import org.rostilos.codecrow.core.persistence.repository.taskmanagement.TaskManagementConnectionRepository;
import org.rostilos.codecrow.taskmanagement.ETaskManagementPlatform;
import org.rostilos.codecrow.taskmanagement.TaskManagementClient;
import org.rostilos.codecrow.taskmanagement.TaskManagementClientFactory;
import org.rostilos.codecrow.taskmanagement.TaskManagementException;
import org.rostilos.codecrow.taskmanagement.model.TaskDetails;
import org.slf4j.Logger;
import org.slf4j.LoggerFactory;
import org.springframework.stereotype.Service;

import java.util.Arrays;
import java.util.Collections;
import java.util.LinkedHashMap;
import java.util.List;
import java.util.Map;
import java.util.Optional;
import java.util.regex.Matcher;
import java.util.regex.Pattern;
import java.util.regex.PatternSyntaxException;

/**
 * Resolves optional task-management context for PR analysis.
 *
 * <p>Review analysis should benefit from Jira context when it is available,
 * but it must remain non-critical: failures here never block code review.</p>
 */
@Service
public class TaskContextEnrichmentService {

    private static final Logger log = LoggerFactory.getLogger(TaskContextEnrichmentService.class);

    private final TaskManagementConnectionRepository connectionRepository;
    private final TaskManagementClientFactory clientFactory;

    public TaskContextEnrichmentService(TaskManagementConnectionRepository connectionRepository,
                                        TaskManagementClientFactory clientFactory) {
        this.connectionRepository = connectionRepository;
        this.clientFactory = clientFactory;
    }

    public Map<String, String> resolveTaskContext(Project project,
                                                   String sourceBranch,
                                                   String prTitle,
                                                   String prDescription) {
        if (project == null || project.getWorkspace() == null || project.getWorkspace().getId() == null) {
            return Collections.emptyMap();
        }

        try {
            var projectConfig = project.getEffectiveConfig();
            if (!projectConfig.isTaskContextAnalysisEnabled()) {
                log.debug("Task context: disabled for project {}", project.getId());
                return Collections.emptyMap();
            }

            QaAutoDocConfig qaConfig = projectConfig.getQaAutoDocConfig();
            Optional<TaskManagementConnection> connection = resolveConnection(project, qaConfig);
            if (connection.isEmpty()) {
                return Collections.emptyMap();
            }

            String taskId = extractTaskId(qaConfig, sourceBranch, prTitle, prDescription);
            if (taskId == null) {
                log.debug("Task context: no task id found for project {}", project.getId());
                return Collections.emptyMap();
            }

            TaskManagementClient client = createClient(connection.get());
            TaskDetails taskDetails = client.getTaskDetails(taskId);
            Map<String, String> context = toTaskContext(taskDetails, connection.get());

            log.info("Task context: fetched {} from {} for project {}",
                    taskId, connection.get().getProviderType().getId(), project.getId());
            return context;
        } catch (TaskManagementException e) {
            if (e.getStatusCode() == 404) {
                log.info("Task context: linked task was not found: {}", e.getMessage());
            } else if (e.isAuthError()) {
                log.warn("Task context: authentication failed: {}", e.getMessage());
            } else {
                log.warn("Task context: task-management lookup failed: {}", e.getMessage());
            }
            return Collections.emptyMap();
        } catch (Exception e) {
            log.warn("Task context: failed to resolve task context for project {} (non-critical): {}",
                    project.getId(), e.getMessage());
            return Collections.emptyMap();
        }
    }

    private Optional<TaskManagementConnection> resolveConnection(Project project, QaAutoDocConfig qaConfig) {
        Long workspaceId = project.getWorkspace().getId();

        if (qaConfig.taskManagementConnectionId() != null) {
            Optional<TaskManagementConnection> configured = connectionRepository.findByIdAndWorkspaceId(
                    qaConfig.taskManagementConnectionId(), workspaceId);
            if (configured.isPresent()) {
                return configured;
            }
            log.warn("Task context: configured task-management connection {} not found in workspace {}",
                    qaConfig.taskManagementConnectionId(), workspaceId);
            return Optional.empty();
        }

        List<TaskManagementConnection> supported = connectionRepository.findByWorkspaceId(workspaceId)
                .stream()
                .filter(conn -> conn.getProviderType() != null && conn.getProviderType().isSupported())
                .toList();

        List<TaskManagementConnection> connected = supported.stream()
                .filter(conn -> conn.getStatus() == ETaskManagementConnectionStatus.CONNECTED)
                .toList();
        if (connected.size() == 1) {
            return Optional.of(connected.get(0));
        }

        if (supported.size() > 1) {
            log.debug("Task context: multiple task-management connections in workspace {}; configure QA auto-doc connection to disambiguate",
                    workspaceId);
        }
        return Optional.empty();
    }

    private String extractTaskId(QaAutoDocConfig qaConfig,
                                 String sourceBranch,
                                 String prTitle,
                                 String prDescription) {
        Pattern pattern = compileTaskPattern(qaConfig);

        if (qaConfig.taskManagementConnectionId() != null) {
            String configuredSource = sourceValue(
                    qaConfig.effectiveTaskIdSource(),
                    sourceBranch,
                    prTitle,
                    prDescription);
            String taskId = firstMatch(pattern, configuredSource);
            if (taskId != null) {
                return taskId;
            }
        }

        for (String candidate : Arrays.asList(sourceBranch, prTitle, prDescription)) {
            String taskId = firstMatch(pattern, candidate);
            if (taskId != null) {
                return taskId;
            }
        }
        return null;
    }

    private Pattern compileTaskPattern(QaAutoDocConfig qaConfig) {
        String pattern = qaConfig.effectiveTaskIdPattern();
        try {
            return Pattern.compile(pattern);
        } catch (PatternSyntaxException e) {
            log.warn("Task context: invalid task id pattern '{}', falling back to default: {}",
                    pattern, e.getDescription());
            return Pattern.compile(QaAutoDocConfig.DEFAULT_TASK_ID_PATTERN);
        }
    }

    private String sourceValue(QaAutoDocConfig.TaskIdSource source,
                               String sourceBranch,
                               String prTitle,
                               String prDescription) {
        return switch (source) {
            case BRANCH_NAME -> sourceBranch;
            case PR_TITLE -> prTitle;
            case PR_DESCRIPTION -> prDescription;
        };
    }

    private String firstMatch(Pattern pattern, String value) {
        if (value == null || value.isBlank()) {
            return null;
        }
        Matcher matcher = pattern.matcher(value);
        return matcher.find() ? matcher.group() : null;
    }

    private TaskManagementClient createClient(TaskManagementConnection conn) {
        ETaskManagementPlatform platform = ETaskManagementPlatform.fromId(conn.getProviderType().getId());
        Map<String, String> creds = conn.getCredentials() != null ? conn.getCredentials() : Map.of();
        return clientFactory.createClient(
                platform,
                conn.getBaseUrl(),
                creds.getOrDefault("email", ""),
                creds.getOrDefault("apiToken", "")
        );
    }

    private Map<String, String> toTaskContext(TaskDetails details, TaskManagementConnection connection) {
        Map<String, String> context = new LinkedHashMap<>();
        putIfNotBlank(context, "task_key", details.taskId());
        putIfNotBlank(context, "task_summary", details.summary());
        putIfNotBlank(context, "description", details.description());
        putIfNotBlank(context, "status", details.status());
        putIfNotBlank(context, "task_type", details.taskType());
        putIfNotBlank(context, "priority", details.priority());
        putIfNotBlank(context, "assignee", details.assignee());
        putIfNotBlank(context, "reporter", details.reporter());
        putIfNotBlank(context, "web_url", details.webUrl());
        putIfNotBlank(context, "provider", connection.getProviderType().getId());
        return context;
    }

    private void putIfNotBlank(Map<String, String> context, String key, String value) {
        if (value != null && !value.isBlank()) {
            context.put(key, value);
        }
    }
}
