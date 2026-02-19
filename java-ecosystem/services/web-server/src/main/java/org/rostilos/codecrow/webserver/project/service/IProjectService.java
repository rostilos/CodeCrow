package org.rostilos.codecrow.webserver.project.service;

import java.security.GeneralSecurityException;
import java.util.List;

import org.rostilos.codecrow.core.model.branch.Branch;
import org.rostilos.codecrow.core.model.project.Project;
import org.rostilos.codecrow.core.model.project.config.BranchAnalysisConfig;
import org.rostilos.codecrow.core.model.project.config.CommentCommandsConfig;
import org.rostilos.codecrow.core.model.project.config.InstallationMethod;
import org.rostilos.codecrow.core.model.project.config.ProjectRulesConfig;
import org.rostilos.codecrow.core.model.vcs.EVcsProvider;
import org.rostilos.codecrow.webserver.project.dto.request.BindAiConnectionRequest;
import org.rostilos.codecrow.webserver.project.dto.request.BindRepositoryRequest;
import org.rostilos.codecrow.webserver.project.dto.request.ChangeVcsConnectionRequest;
import org.rostilos.codecrow.webserver.project.dto.request.CreateProjectRequest;
import org.rostilos.codecrow.webserver.project.dto.request.UpdateCommentCommandsConfigRequest;
import org.rostilos.codecrow.webserver.project.dto.request.UpdateProjectRequest;
import org.rostilos.codecrow.webserver.project.dto.request.UpdateProjectRulesRequest;
import org.rostilos.codecrow.webserver.project.dto.request.UpdateRepositorySettingsRequest;
import org.springframework.data.domain.Page;

/**
 * Interface for project management operations.
 * <p>
 * This interface enables cloud implementations to extend or decorate
 * the base project service with additional capabilities like billing limits.
 */
public interface IProjectService {

    // ==================== Core CRUD ====================

    List<Project> listWorkspaceProjects(Long workspaceId);

    Page<Project> listWorkspaceProjectsPaginated(Long workspaceId, String search, int page, int size);

    Project createProject(Long workspaceId, CreateProjectRequest request);

    Project getProjectById(Long projectId);

    Project getProjectByWorkspaceAndNamespace(Long workspaceId, String namespace);

    Project updateProject(Long workspaceId, Long projectId, UpdateProjectRequest request);

    void deleteProject(Long workspaceId, Long projectId);

    void deleteProjectByNamespace(Long workspaceId, String namespace);

    // ==================== Repository Binding ====================

    Project bindRepository(Long workspaceId, Long projectId, BindRepositoryRequest request);

    Project unbindRepository(Long workspaceId, Long projectId);

    void updateRepositorySettings(Long workspaceId, Long projectId, UpdateRepositorySettingsRequest request)
            throws GeneralSecurityException;

    Project changeVcsConnection(Long workspaceId, Long projectId, ChangeVcsConnectionRequest request);

    // ==================== AI Connection ====================

    boolean bindAiConnection(Long workspaceId, Long projectId, BindAiConnectionRequest request);

    // ==================== Branch Management ====================

    List<Branch> getProjectBranches(Long workspaceId, String namespace);

    Project setDefaultBranch(Long workspaceId, String namespace, Long branchId);

    Project setDefaultBranchByName(Long workspaceId, String namespace, String branchName);

    // ==================== Configuration ====================

    BranchAnalysisConfig getBranchAnalysisConfig(Project project);

    Project updateBranchAnalysisConfig(Long workspaceId, Long projectId,
            List<String> prTargetBranches, List<String> branchPushPatterns);

    Project updateRagConfig(Long workspaceId, Long projectId, boolean enabled, String branch, List<String> includePatterns,
            List<String> excludePatterns, Boolean multiBranchEnabled, Integer branchRetentionDays);

    Project updateRagConfig(Long workspaceId, Long projectId, boolean enabled, String branch, List<String> includePatterns,
            List<String> excludePatterns);

    Project updateAnalysisSettings(Long workspaceId, Long projectId, Boolean prAnalysisEnabled,
            Boolean branchAnalysisEnabled, InstallationMethod installationMethod, Integer maxAnalysisTokenLimit);

    Project updateProjectQualityGate(Long workspaceId, Long projectId, Long qualityGateId);

    CommentCommandsConfig getCommentCommandsConfig(Project project);

    Project updateCommentCommandsConfig(Long workspaceId, Long projectId, UpdateCommentCommandsConfigRequest request);

    // ==================== Project Rules ====================

    ProjectRulesConfig getProjectRulesConfig(Project project);

    Project updateProjectRules(Long workspaceId, Long projectId, UpdateProjectRulesRequest request);

    // ==================== Webhooks ====================

    WebhookSetupResult setupWebhooks(Long workspaceId, Long projectId);

    WebhookInfo getWebhookInfo(Long workspaceId, Long projectId);

    // ==================== DTOs ====================

    record WebhookSetupResult(
            boolean success,
            String webhookId,
            String webhookUrl,
            String message) {
    }

    record WebhookInfo(
            boolean webhooksConfigured,
            String webhookId,
            String webhookUrl,
            EVcsProvider provider) {
    }
}
