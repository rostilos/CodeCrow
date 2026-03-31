package org.rostilos.codecrow.webserver.taskmanagement.service;

import org.rostilos.codecrow.core.dto.taskmanagement.QaAutoDocConfigRequest;
import org.rostilos.codecrow.core.dto.taskmanagement.TaskManagementConnectionRequest;
import org.rostilos.codecrow.core.dto.taskmanagement.TaskManagementConnectionResponse;
import org.rostilos.codecrow.core.model.project.Project;
import org.rostilos.codecrow.core.model.project.config.ProjectConfig;
import org.rostilos.codecrow.core.model.project.config.QaAutoDocConfig;
import org.rostilos.codecrow.core.model.taskmanagement.ETaskManagementConnectionStatus;
import org.rostilos.codecrow.core.model.taskmanagement.ETaskManagementProvider;
import org.rostilos.codecrow.core.model.taskmanagement.TaskManagementConnection;
import org.rostilos.codecrow.core.persistence.repository.project.ProjectRepository;
import org.rostilos.codecrow.core.persistence.repository.taskmanagement.TaskManagementConnectionRepository;
import org.rostilos.codecrow.taskmanagement.ETaskManagementPlatform;
import org.rostilos.codecrow.taskmanagement.TaskManagementClient;
import org.rostilos.codecrow.taskmanagement.TaskManagementClientFactory;
import org.rostilos.codecrow.taskmanagement.TaskManagementException;
import org.slf4j.Logger;
import org.slf4j.LoggerFactory;
import org.springframework.stereotype.Service;
import org.springframework.transaction.annotation.Transactional;

import java.util.LinkedHashMap;
import java.util.List;
import java.util.Map;
import java.util.regex.Pattern;
import java.util.regex.PatternSyntaxException;

/**
 * Service for managing task management connections and QA auto-documentation config.
 */
@Service
public class TaskManagementService {

    private static final Logger log = LoggerFactory.getLogger(TaskManagementService.class);

    /**
     * Injection patterns that must be rejected in custom QA templates.
     * Reuses the same detection strategy as PromptSanitizationService.
     */
    private static final List<Pattern> INJECTION_PATTERNS = List.of(
            Pattern.compile("(?i)ignore\\s+(all\\s+)?previous\\s+instructions"),
            Pattern.compile("(?i)ignore\\s+(all\\s+)?above\\s+instructions"),
            Pattern.compile("(?i)disregard\\s+(all\\s+)?previous"),
            Pattern.compile("(?i)you\\s+are\\s+now\\s+"),
            Pattern.compile("(?i)forget\\s+(all\\s+)?previous"),
            Pattern.compile("(?i)override\\s+system\\s+prompt"),
            Pattern.compile("(?i)new\\s+system\\s+prompt"),
            Pattern.compile("(?i)\\bsystem\\s*:\\s*"),
            Pattern.compile("(?i)\\bassistant\\s*:\\s*"),
            Pattern.compile("(?i)\\buser\\s*:\\s*"),
            Pattern.compile("(?i)```\\s*system"),
            Pattern.compile("###\\s*(?:system|instruction|prompt)"),
            Pattern.compile("(?i)<\\|(?:system|im_start|im_end)\\|>"),
            Pattern.compile("(?i)\\[INST\\]"),
            Pattern.compile("(?i)\\[/INST\\]"),
            Pattern.compile("(?i)print\\s+(?:all|your|the)\\s+(?:instructions|prompts|system)"),
            Pattern.compile("(?i)reveal\\s+(?:your|the)\\s+(?:instructions|prompts|system)"),
            Pattern.compile("(?i)(?:exec|eval|os\\.system|subprocess|__import__|compile)\\s*\\(")
    );

    private final TaskManagementConnectionRepository connectionRepository;
    private final TaskManagementClientFactory clientFactory;
    private final ProjectRepository projectRepository;

    public TaskManagementService(TaskManagementConnectionRepository connectionRepository,
                                  TaskManagementClientFactory clientFactory,
                                  ProjectRepository projectRepository) {
        this.connectionRepository = connectionRepository;
        this.clientFactory = clientFactory;
        this.projectRepository = projectRepository;
    }

    // ─── Connection CRUD ─────────────────────────────────────────────

    public List<TaskManagementConnectionResponse> listConnections(Long workspaceId) {
        return connectionRepository.findByWorkspaceId(workspaceId)
                .stream()
                .map(this::toResponse)
                .toList();
    }

    public TaskManagementConnectionResponse getConnection(Long workspaceId, Long connectionId) {
        TaskManagementConnection conn = connectionRepository.findByIdAndWorkspaceId(connectionId, workspaceId)
                .orElseThrow(() -> new IllegalArgumentException("Connection not found: " + connectionId));
        return toResponse(conn);
    }

    @Transactional
    public TaskManagementConnectionResponse createConnection(Long workspaceId,
                                                              org.rostilos.codecrow.core.model.workspace.Workspace workspace,
                                                              TaskManagementConnectionRequest request) {
        ETaskManagementProvider provider = ETaskManagementProvider.fromId(request.providerType());

        if (!provider.isSupported()) {
            throw new UnsupportedOperationException(
                    provider.getId() + " support is coming soon. Please use Jira Cloud for now.");
        }

        // Provider-specific credential validation
        validateCredentialsForProvider(provider, request);

        if (connectionRepository.existsByWorkspaceIdAndConnectionName(workspaceId, request.connectionName())) {
            throw new IllegalArgumentException("A connection named '" + request.connectionName()
                                               + "' already exists in this workspace.");
        }

        TaskManagementConnection conn = new TaskManagementConnection();
        conn.setWorkspace(workspace);
        conn.setConnectionName(request.connectionName());
        conn.setProviderType(provider);
        conn.setStatus(ETaskManagementConnectionStatus.PENDING);
        conn.setBaseUrl(request.baseUrl());
        conn.setCredentials(buildCredentials(request));

        conn = connectionRepository.save(conn);
        log.info("Created task management connection '{}' (id={}) for workspace {}",
                request.connectionName(), conn.getId(), workspaceId);

        // Auto-validate so the connection transitions out of PENDING immediately
        try {
            TaskManagementClient client = createClientFromConnection(conn);
            boolean valid = client.validateConnection();
            conn.setStatus(valid
                    ? ETaskManagementConnectionStatus.CONNECTED
                    : ETaskManagementConnectionStatus.ERROR);
            conn = connectionRepository.save(conn);
            log.info("Auto-validation for connection '{}' (id={}): {}",
                    request.connectionName(), conn.getId(), conn.getStatus());
        } catch (Exception e) {
            log.warn("Auto-validation failed for connection '{}' (id={}): {}",
                    request.connectionName(), conn.getId(), e.getMessage());
            conn.setStatus(ETaskManagementConnectionStatus.ERROR);
            conn = connectionRepository.save(conn);
        }

        return toResponse(conn);
    }

    @Transactional
    public TaskManagementConnectionResponse updateConnection(Long workspaceId, Long connectionId,
                                                              TaskManagementConnectionRequest request) {
        TaskManagementConnection conn = connectionRepository.findByIdAndWorkspaceId(connectionId, workspaceId)
                .orElseThrow(() -> new IllegalArgumentException("Connection not found: " + connectionId));

        conn.setConnectionName(request.connectionName());
        conn.setBaseUrl(request.baseUrl());
        conn.setCredentials(buildCredentials(request));
        conn.setStatus(ETaskManagementConnectionStatus.PENDING); // Re-validate after credential change

        conn = connectionRepository.save(conn);
        log.info("Updated task management connection '{}' (id={})", request.connectionName(), connectionId);
        return toResponse(conn);
    }

    @Transactional
    public void deleteConnection(Long workspaceId, Long connectionId) {
        connectionRepository.deleteByIdAndWorkspaceId(connectionId, workspaceId);
        log.info("Deleted task management connection id={} from workspace {}", connectionId, workspaceId);
    }

    // ─── Connection validation ───────────────────────────────────────

    @Transactional
    public TaskManagementConnectionResponse validateConnection(Long workspaceId, Long connectionId) {
        TaskManagementConnection conn = connectionRepository.findByIdAndWorkspaceId(connectionId, workspaceId)
                .orElseThrow(() -> new IllegalArgumentException("Connection not found: " + connectionId));

        try {
            TaskManagementClient client = createClientFromConnection(conn);
            boolean valid = client.validateConnection();
            conn.setStatus(valid
                    ? ETaskManagementConnectionStatus.CONNECTED
                    : ETaskManagementConnectionStatus.ERROR);
        } catch (TaskManagementException e) {
            log.warn("Task management connection validation failed (id={}): {}", connectionId, e.getMessage());
            conn.setStatus(ETaskManagementConnectionStatus.ERROR);
        } catch (Exception e) {
            log.error("Unexpected error validating task management connection (id={}): {}",
                    connectionId, e.getMessage(), e);
            conn.setStatus(ETaskManagementConnectionStatus.ERROR);
        }

        conn = connectionRepository.save(conn);
        return toResponse(conn);
    }

    // ─── QA Auto-Doc Configuration ───────────────────────────────────

    @Transactional
    public QaAutoDocConfig updateQaAutoDocConfig(Long workspaceId, Long projectId, QaAutoDocConfigRequest request) {
        Project project = projectRepository.findById(projectId)
                .orElseThrow(() -> new IllegalArgumentException("Project not found: " + projectId));

        // Verify project belongs to the resolved workspace (prevents BOLA)
        if (!project.getWorkspace().getId().equals(workspaceId)) {
            throw new IllegalArgumentException("Project not found in workspace");
        }

        // Validate task ID pattern is a valid regex
        if (request.taskIdPattern() != null && !request.taskIdPattern().isBlank()) {
            try {
                Pattern.compile(request.taskIdPattern());
            } catch (PatternSyntaxException e) {
                throw new IllegalArgumentException("Invalid task ID pattern regex: " + e.getDescription());
            }
        }

        // Validate template mode and custom template
        QaAutoDocConfig.TemplateMode mode = request.templateMode() != null
                ? QaAutoDocConfig.TemplateMode.valueOf(request.templateMode())
                : QaAutoDocConfig.TemplateMode.BASE;

        String customTemplate = null;
        if (mode == QaAutoDocConfig.TemplateMode.CUSTOM) {
            customTemplate = request.customTemplate();
            if (customTemplate == null || customTemplate.isBlank()) {
                throw new IllegalArgumentException("Custom template is required when template mode is CUSTOM.");
            }
            if (customTemplate.length() > QaAutoDocConfig.MAX_CUSTOM_TEMPLATE_LENGTH) {
                throw new IllegalArgumentException("Custom template exceeds maximum length of "
                                                   + QaAutoDocConfig.MAX_CUSTOM_TEMPLATE_LENGTH + " characters.");
            }
            validateTemplateForInjection(customTemplate);
        }

        QaAutoDocConfig.TaskIdSource source = request.taskIdSource() != null
                ? QaAutoDocConfig.TaskIdSource.valueOf(request.taskIdSource())
                : QaAutoDocConfig.TaskIdSource.BRANCH_NAME;

        QaAutoDocConfig qaConfig = new QaAutoDocConfig(
                request.enabled(),
                request.taskManagementConnectionId(),
                request.taskIdPattern() != null ? request.taskIdPattern() : QaAutoDocConfig.DEFAULT_TASK_ID_PATTERN,
                source,
                mode,
                customTemplate,
                request.outputLanguage()
        );

        // Update project config
        ProjectConfig config = project.getConfiguration();
        if (config == null) {
            config = new ProjectConfig();
        }
        config.setQaAutoDoc(qaConfig);
        project.setConfiguration(config);
        projectRepository.save(project);

        log.info("Updated QA auto-doc config for project {} (enabled={}, mode={})",
                projectId, qaConfig.enabled(), qaConfig.effectiveTemplateMode());
        return qaConfig;
    }

    // ─── Template sanitization ───────────────────────────────────────

    /**
     * Validate a custom QA template for prompt injection attempts.
     * Reuses injection detection patterns from PromptSanitizationService.
     *
     * @throws IllegalArgumentException if injection patterns are detected
     */
    public void validateTemplateForInjection(String template) {
        if (template == null || template.isBlank()) {
            return;
        }
        for (Pattern pattern : INJECTION_PATTERNS) {
            if (pattern.matcher(template).find()) {
                throw new IllegalArgumentException(
                        "Custom template contains disallowed content that resembles prompt injection. "
                        + "Please remove instructions that attempt to override system behavior.");
            }
        }
    }

    // ─── Helpers ─────────────────────────────────────────────────────

    /**
     * Validate that the request contains the credentials required by the given provider.
     *
     * @throws IllegalArgumentException if required credentials are missing
     */
    private void validateCredentialsForProvider(ETaskManagementProvider provider,
                                                 TaskManagementConnectionRequest request) {
        switch (provider) {
            case JIRA_CLOUD -> {
                if (request.email() == null || request.email().isBlank()) {
                    throw new IllegalArgumentException("Email is required for Jira Cloud authentication");
                }
                if (request.apiToken() == null || request.apiToken().isBlank()) {
                    throw new IllegalArgumentException("API token is required for Jira Cloud authentication");
                }
            }
            // Future providers (e.g. JIRA_DATA_CENTER) may only require apiToken (PAT)
            default -> {
                if (request.apiToken() == null || request.apiToken().isBlank()) {
                    throw new IllegalArgumentException("API token is required");
                }
            }
        }
    }

    public TaskManagementClient createClientFromConnection(TaskManagementConnection conn) {
        ETaskManagementPlatform platform = ETaskManagementPlatform.fromId(conn.getProviderType().getId());
        Map<String, String> creds = conn.getCredentials();
        return clientFactory.createClient(
                platform,
                conn.getBaseUrl(),
                creds.getOrDefault("email", ""),
                creds.getOrDefault("apiToken", "")
        );
    }

    private Map<String, String> buildCredentials(TaskManagementConnectionRequest request) {
        Map<String, String> creds = new LinkedHashMap<>();
        creds.put("email", request.email());
        creds.put("apiToken", request.apiToken());
        return creds;
    }

    private TaskManagementConnectionResponse toResponse(TaskManagementConnection conn) {
        String maskedEmail = maskEmail(conn.getCredentials().getOrDefault("email", ""));
        return new TaskManagementConnectionResponse(
                conn.getId(),
                conn.getConnectionName(),
                conn.getProviderType().name(),
                conn.getStatus().name(),
                conn.getBaseUrl(),
                maskedEmail,
                conn.getCreatedAt(),
                conn.getUpdatedAt()
        );
    }

    private String maskEmail(String email) {
        if (email == null || email.isBlank()) return "";
        int atIdx = email.indexOf('@');
        if (atIdx <= 1) return email;
        return email.charAt(0) + "***" + email.substring(atIdx);
    }
}
