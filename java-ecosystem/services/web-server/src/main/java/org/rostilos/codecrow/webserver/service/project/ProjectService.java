package org.rostilos.codecrow.webserver.service.project;

import java.security.GeneralSecurityException;
import java.security.SecureRandom;
import java.util.Base64;
import java.util.List;
import java.util.NoSuchElementException;

import org.rostilos.codecrow.core.model.ai.AIConnection;
import org.rostilos.codecrow.core.model.branch.Branch;
import org.rostilos.codecrow.core.model.project.Project;
import org.rostilos.codecrow.core.model.project.ProjectAiConnectionBinding;
import org.rostilos.codecrow.core.model.project.ProjectVcsConnectionBinding;
import org.rostilos.codecrow.core.model.vcs.VcsConnection;
import org.rostilos.codecrow.core.model.vcs.config.cloud.BitbucketCloudConfig;
import org.rostilos.codecrow.core.model.workspace.Workspace;
import org.rostilos.codecrow.core.persistence.repository.ai.AiConnectionRepository;
import org.rostilos.codecrow.core.persistence.repository.analysis.AnalysisLockRepository;
import org.rostilos.codecrow.core.persistence.repository.analysis.RagIndexStatusRepository;
import org.rostilos.codecrow.core.persistence.repository.branch.BranchFileRepository;
import org.rostilos.codecrow.core.persistence.repository.branch.BranchIssueRepository;
import org.rostilos.codecrow.core.persistence.repository.branch.BranchRepository;
import org.rostilos.codecrow.core.persistence.repository.codeanalysis.CodeAnalysisRepository;
import org.rostilos.codecrow.core.persistence.repository.permission.ProjectPermissionAssignmentRepository;
import org.rostilos.codecrow.core.persistence.repository.project.ProjectRepository;
import org.rostilos.codecrow.core.persistence.repository.project.ProjectTokenRepository;
import org.rostilos.codecrow.core.persistence.repository.pullrequest.PullRequestRepository;
import org.rostilos.codecrow.core.persistence.repository.vcs.VcsConnectionRepository;
import org.rostilos.codecrow.core.persistence.repository.vcs.VcsRepoBindingRepository;
import org.rostilos.codecrow.core.persistence.repository.workspace.WorkspaceRepository;
import org.rostilos.codecrow.core.model.project.config.ProjectConfig;
import org.rostilos.codecrow.security.oauth.TokenEncryptionService;
import org.rostilos.codecrow.webserver.dto.request.project.BindAiConnectionRequest;
import org.rostilos.codecrow.webserver.dto.request.project.BindRepositoryRequest;
import org.rostilos.codecrow.webserver.dto.request.project.CreateProjectRequest;
import org.rostilos.codecrow.webserver.dto.request.project.UpdateProjectRequest;
import org.rostilos.codecrow.webserver.dto.request.project.UpdateRepositorySettingsRequest;
import org.rostilos.codecrow.webserver.exception.InvalidProjectRequestException;
import org.springframework.stereotype.Service;
import org.springframework.transaction.annotation.Transactional;

import io.jsonwebtoken.security.SecurityException;

@Service
public class ProjectService {
    private final ProjectRepository projectRepository;
    private final VcsConnectionRepository vcsConnectionRepository;
    private final TokenEncryptionService tokenEncryptionService;
    private final AiConnectionRepository aiConnectionRepository;
    private final WorkspaceRepository workspaceRepository;
    private final BranchRepository branchRepository;
    private final BranchFileRepository branchFileRepository;
    private final BranchIssueRepository branchIssueRepository;
    private final CodeAnalysisRepository codeAnalysisRepository;
    private final ProjectTokenRepository projectTokenRepository;
    private final PullRequestRepository pullRequestRepository;
    private final VcsRepoBindingRepository vcsRepoBindingRepository;
    private final ProjectPermissionAssignmentRepository permissionAssignmentRepository;
    private final AnalysisLockRepository analysisLockRepository;
    private final RagIndexStatusRepository ragIndexStatusRepository;

    public ProjectService(
            ProjectRepository projectRepository,
            VcsConnectionRepository vcsConnectionRepository,
            TokenEncryptionService tokenEncryptionService,
            AiConnectionRepository aiConnectionRepository,
            WorkspaceRepository workspaceRepository,
            BranchRepository branchRepository,
            BranchFileRepository branchFileRepository,
            BranchIssueRepository branchIssueRepository,
            CodeAnalysisRepository codeAnalysisRepository,
            ProjectTokenRepository projectTokenRepository,
            PullRequestRepository pullRequestRepository,
            VcsRepoBindingRepository vcsRepoBindingRepository,
            ProjectPermissionAssignmentRepository permissionAssignmentRepository,
            AnalysisLockRepository analysisLockRepository,
            RagIndexStatusRepository ragIndexStatusRepository
    ) {
        this.projectRepository = projectRepository;
        this.vcsConnectionRepository = vcsConnectionRepository;
        this.tokenEncryptionService = tokenEncryptionService;
        this.aiConnectionRepository = aiConnectionRepository;
        this.workspaceRepository = workspaceRepository;
        this.branchRepository = branchRepository;
        this.branchFileRepository = branchFileRepository;
        this.branchIssueRepository = branchIssueRepository;
        this.codeAnalysisRepository = codeAnalysisRepository;
        this.projectTokenRepository = projectTokenRepository;
        this.pullRequestRepository = pullRequestRepository;
        this.vcsRepoBindingRepository = vcsRepoBindingRepository;
        this.permissionAssignmentRepository = permissionAssignmentRepository;
        this.analysisLockRepository = analysisLockRepository;
        this.ragIndexStatusRepository = ragIndexStatusRepository;
    }

    @Transactional(readOnly = true)
    public List<Project> listWorkspaceProjects(Long workspaceId) {
        // Use the method that fetches default branch eagerly to include stats in project list
        return projectRepository.findByWorkspaceIdWithDefaultBranch(workspaceId);
    }

    @Transactional
    public Project createProject(Long workspaceId, CreateProjectRequest request) throws SecurityException {
        validateCreateProjectRequest(request);

        if (request.getNamespace() == null || request.getNamespace().trim().isEmpty()) {
            throw new InvalidProjectRequestException("Project namespace is required");
        }

        Workspace ws = workspaceRepository.findById(workspaceId)
                .orElseThrow(() -> new NoSuchElementException("Workspace not found"));

        // ensure namespace uniqueness per workspace
        projectRepository.findByWorkspaceIdAndNamespace(workspaceId, request.getNamespace())
                .ifPresent(p -> { throw new InvalidProjectRequestException("Project namespace already exists in workspace"); });

        Project newProject = new Project();
        newProject.setWorkspace(ws);
        newProject.setName(request.getName());
        newProject.setNamespace(request.getNamespace());
        newProject.setDescription(request.getDescription());
        newProject.setIsActive(true);

        // persist default branch into project configuration (if provided)
        String defaultBranch = null;
        if (request.getDefaultBranch() != null && !request.getDefaultBranch().isBlank()) {
            defaultBranch = request.getDefaultBranch();
        }
        newProject.setConfiguration(new ProjectConfig(false, defaultBranch));

        if (request.hasVcsConnection()) {
            VcsConnection vcsConnection = vcsConnectionRepository.findByWorkspace_IdAndId(workspaceId, request.getVcsConnectionId())
                    .orElseThrow(() -> new NoSuchElementException("VCS connection not found!"));

            ProjectVcsConnectionBinding vcsBinding = new ProjectVcsConnectionBinding();
            vcsBinding.setProject(newProject);
            vcsBinding.setVcsProvider(request.getVcsProvider());
            vcsBinding.setVcsConnection(vcsConnection);
            vcsBinding.setRepoSlug(request.getRepositorySlug());
            vcsBinding.setRepositoryUUID(request.getRepositoryUUID());
            vcsBinding.setWorkspace(((BitbucketCloudConfig) vcsConnection.getConfiguration()).workspaceId());
            newProject.setVcsBinding(vcsBinding);
        }

        // generate internal auth token for the project
        try {
            byte[] random = new byte[32];
            new SecureRandom().nextBytes(random);
            String plainToken = Base64.getUrlEncoder().withoutPadding().encodeToString(random);
            String encrypted = tokenEncryptionService.encrypt(plainToken);
            newProject.setAuthToken(encrypted);
            // Note: plainToken is not returned in API responses; store encrypted token only.
        } catch (GeneralSecurityException e) {
            throw new SecurityException("Failed to generate project auth token");
        }

        return projectRepository.save(newProject);
    }

    @Transactional(readOnly = true)
    public Project getProjectById(Long projectId) {
        return projectRepository.findById(projectId)
                .orElseThrow(() -> new NoSuchElementException("Project not found"));
    }

    @Transactional(readOnly = true)
    public Project getProjectByWorkspaceAndNamespace(Long workspaceId, String namespace) {
        return projectRepository.findByWorkspaceIdAndNamespace(workspaceId, namespace)
                .orElseThrow(() -> new NoSuchElementException("Project not found"));
    }

    @Transactional
    public void deleteProjectByNamespace(Long workspaceId, String namespace) {
        Project project = projectRepository.findByWorkspaceIdAndNamespace(workspaceId, namespace)
                .orElseThrow(() -> new NoSuchElementException("Project not found"));
        
        Long projectId = project.getId();
        
        // Clear the default branch reference first to avoid circular FK constraint
        project.setDefaultBranch(null);
        projectRepository.save(project);
        
        // Delete all related entities in correct order (respect FK constraints)
        branchIssueRepository.deleteByProjectId(projectId);
        codeAnalysisRepository.deleteByProjectId(projectId);
        branchFileRepository.deleteByProjectId(projectId);
        branchRepository.deleteByProjectId(projectId);
        pullRequestRepository.deleteByProject_Id(projectId);
        projectTokenRepository.deleteByProject_Id(projectId);
        vcsRepoBindingRepository.deleteByProject_Id(projectId);
        permissionAssignmentRepository.deleteByProject_Id(projectId);
        analysisLockRepository.deleteByProjectId(projectId);
        ragIndexStatusRepository.deleteByProjectId(projectId);
        
        // Finally delete the project (cascade will handle vcsBinding and aiBinding)
        projectRepository.delete(project);
    }

    @Transactional
    public Project updateProject(Long workspaceId, Long projectId, UpdateProjectRequest request) {
        Project project = projectRepository.findByWorkspaceIdAndId(workspaceId, projectId)
                .orElseThrow(() -> new NoSuchElementException("Project not found"));

        if (request.getName() != null) {
            project.setName(request.getName());
        }
        
        // Namespace is immutable - reject any attempt to change it
        if (request.getNamespace() != null && !request.getNamespace().trim().isEmpty() 
                && !request.getNamespace().equals(project.getNamespace())) {
            throw new InvalidProjectRequestException("Project namespace cannot be changed after creation");
        }

        if (request.getDescription() != null) {
            project.setDescription(request.getDescription());
        }

        // Update default branch in project configuration if provided
        if (request.getDefaultBranch() != null) {
            var cfg = project.getConfiguration();
            boolean useLocal = cfg == null ? false : cfg.useLocalMcp();
            var branchAnalysis = cfg != null ? cfg.branchAnalysis() : null;
            var ragConfig = cfg != null ? cfg.ragConfig() : null;
            project.setConfiguration(new ProjectConfig(useLocal, request.getDefaultBranch(), branchAnalysis, ragConfig));
        }

        return projectRepository.save(project);
    }

    @Transactional
    public void deleteProject(Long workspaceId, Long projectId) {
        Project project = projectRepository.findByWorkspaceIdAndId(workspaceId, projectId)
                .orElseThrow(() -> new NoSuchElementException("Project not found"));
        
        // Clear the default branch reference first to avoid circular FK constraint
        project.setDefaultBranch(null);
        projectRepository.save(project);
        
        // Delete all related entities in correct order (respect FK constraints)
        branchIssueRepository.deleteByProjectId(projectId);
        codeAnalysisRepository.deleteByProjectId(projectId);
        branchFileRepository.deleteByProjectId(projectId);
        branchRepository.deleteByProjectId(projectId);
        pullRequestRepository.deleteByProject_Id(projectId);
        projectTokenRepository.deleteByProject_Id(projectId);
        vcsRepoBindingRepository.deleteByProject_Id(projectId);
        permissionAssignmentRepository.deleteByProject_Id(projectId);
        analysisLockRepository.deleteByProjectId(projectId);
        ragIndexStatusRepository.deleteByProjectId(projectId);
        
        // Finally delete the project (cascade will handle vcsBinding and aiBinding)
        projectRepository.delete(project);
    }

    @Transactional
    public Project bindRepository(Long workspaceId, Long projectId, BindRepositoryRequest request) {
        Project p = projectRepository.findByWorkspaceIdAndId(workspaceId, projectId)
                .orElseThrow(() -> new NoSuchElementException("Project not found"));

        if ("BITBUCKET_CLOUD".equalsIgnoreCase(request.getProvider())) {
            VcsConnection conn = vcsConnectionRepository.findByWorkspace_IdAndId(workspaceId, request.getConnectionId())
                    .orElseThrow(() -> new NoSuchElementException("Connection not found"));
        }
        //TODO: bind implementation
        return projectRepository.save(p);
    }

    @Transactional
    public Project unbindRepository(Long workspaceId, Long projectId) {
        Project p = projectRepository.findByWorkspaceIdAndId(workspaceId, projectId)
                .orElseThrow(() -> new NoSuchElementException("Project not found"));
        //TODO: unbind implementation
        // Clear settings placeholder if used in future
        return projectRepository.save(p);
    }


    @Transactional
    public void updateRepositorySettings(Long workspaceId, Long projectId, UpdateRepositorySettingsRequest request) throws GeneralSecurityException {
        Project p = projectRepository.findByWorkspaceIdAndId(workspaceId, projectId)
                .orElseThrow(() -> new NoSuchElementException("Project not found"));

        if (request.getToken() != null && !request.getToken().isBlank()) {
            String encrypted = tokenEncryptionService.encrypt(request.getToken());
            p.setAuthToken(encrypted);
        }
        //TODO: service implementation
        // apiBaseUrl and webhookSecret ignored for now (no fields yet)
        projectRepository.save(p);
    }

    @Transactional
    public boolean bindAiConnection(Long workspaceId, Long projectId, BindAiConnectionRequest request) throws SecurityException {
        Project project = projectRepository.findByWorkspaceIdAndId(workspaceId, projectId)
                .orElseThrow(() -> new NoSuchElementException("Project not found"));

        if (request.getAiConnectionId() != null) {
            Long aiConnectionId = request.getAiConnectionId();
            AIConnection aiConnection = aiConnectionRepository.findByWorkspace_IdAndId(workspaceId, aiConnectionId)
                    .orElseThrow(() -> new NoSuchElementException("Ai connection not found"));

            ProjectAiConnectionBinding aiConnectionBinding = new ProjectAiConnectionBinding();
            aiConnectionBinding.setProject(project);
            aiConnectionBinding.setAiConnection(aiConnection);
            project.setAiConnectionBinding(aiConnectionBinding);
            projectRepository.save(project);
            return true;
        }
        return false;
    }

    private void validateCreateProjectRequest(CreateProjectRequest request) {
        if (request.isImportMode()) {
            if (request.getVcsProvider() == null) {
                throw new InvalidProjectRequestException("VCS provider is required for IMPORT mode");
            }
            if (request.getVcsConnectionId() == null) {
                throw new InvalidProjectRequestException("VCS connection ID is required for IMPORT mode");
            }
            if (request.getRepositorySlug() == null || request.getRepositorySlug().trim().isEmpty()) {
                throw new InvalidProjectRequestException("Repository slug is required for IMPORT mode");
            }
            if (request.getRepositoryUUID() == null) {
                throw new InvalidProjectRequestException("Repository UUID is required for IMPORT mode");
            }
        }
    }

    /**
     * Get list of analyzed branches for a project
     */
    @Transactional(readOnly = true)
    public List<Branch> getProjectBranches(Long workspaceId, String namespace) {
        Project project = getProjectByWorkspaceAndNamespace(workspaceId, namespace);
        return branchRepository.findByProjectId(project.getId());
    }

    /**
     * Set the default branch for a project by branch ID
     */
    @Transactional
    public Project setDefaultBranch(Long workspaceId, String namespace, Long branchId) {
        Project project = getProjectByWorkspaceAndNamespace(workspaceId, namespace);
        
        Branch branch = branchRepository.findById(branchId)
                .orElseThrow(() -> new NoSuchElementException("Branch not found"));
        
        // Verify the branch belongs to this project
        if (!branch.getProject().getId().equals(project.getId())) {
            throw new IllegalArgumentException("Branch does not belong to this project");
        }
        
        project.setDefaultBranch(branch);
        return projectRepository.save(project);
    }

    /**
     * Set the default branch for a project by branch name
     */
    @Transactional
    public Project setDefaultBranchByName(Long workspaceId, String namespace, String branchName) {
        Project project = getProjectByWorkspaceAndNamespace(workspaceId, namespace);
        
        Branch branch = branchRepository.findByProjectIdAndBranchName(project.getId(), branchName)
                .orElseThrow(() -> new NoSuchElementException("Branch '" + branchName + "' not found for this project"));
        
        project.setDefaultBranch(branch);
        return projectRepository.save(project);
    }

    /**
     * Get the branch analysis configuration for a project.
     * Returns a BranchAnalysisConfig record or null if not configured.
     */
    @Transactional(readOnly = true)
    public ProjectConfig.BranchAnalysisConfig getBranchAnalysisConfig(Project project) {
        if (project.getConfiguration() == null) {
            return null;
        }
        return project.getConfiguration().branchAnalysis();
    }

    /**
     * Update the branch analysis configuration for a project.
     * @param workspaceId the workspace ID
     * @param projectId the project ID
     * @param prTargetBranches patterns for PR target branches (e.g., ["main", "develop", "release/*"])
     * @param branchPushPatterns patterns for branch push analysis (e.g., ["main", "develop"])
     * @return the updated project
     */
    @Transactional
    public Project updateBranchAnalysisConfig(
            Long workspaceId,
            Long projectId,
            List<String> prTargetBranches,
            List<String> branchPushPatterns
    ) {
        Project project = projectRepository.findByWorkspaceIdAndId(workspaceId, projectId)
                .orElseThrow(() -> new NoSuchElementException("Project not found"));
        
        ProjectConfig currentConfig = project.getConfiguration();
        boolean useLocalMcp = currentConfig != null && currentConfig.useLocalMcp();
        String defaultBranch = currentConfig != null ? currentConfig.defaultBranch() : null;
        var ragConfig = currentConfig != null ? currentConfig.ragConfig() : null;
        
        ProjectConfig.BranchAnalysisConfig branchConfig = new ProjectConfig.BranchAnalysisConfig(
                prTargetBranches,
                branchPushPatterns
        );
        
        project.setConfiguration(new ProjectConfig(useLocalMcp, defaultBranch, branchConfig, ragConfig));
        return projectRepository.save(project);
    }

    /**
     * Update the RAG configuration for a project.
     * @param workspaceId the workspace ID
     * @param projectId the project ID
     * @param enabled whether RAG indexing is enabled
     * @param branch the branch to index (null uses defaultBranch or 'main')
     * @return the updated project
     */
    @Transactional
    public Project updateRagConfig(
            Long workspaceId,
            Long projectId,
            boolean enabled,
            String branch,
            java.util.List<String> excludePatterns
    ) {
        Project project = projectRepository.findByWorkspaceIdAndId(workspaceId, projectId)
                .orElseThrow(() -> new NoSuchElementException("Project not found"));
        
        ProjectConfig currentConfig = project.getConfiguration();
        boolean useLocalMcp = currentConfig != null && currentConfig.useLocalMcp();
        String defaultBranch = currentConfig != null ? currentConfig.defaultBranch() : null;
        var branchAnalysis = currentConfig != null ? currentConfig.branchAnalysis() : null;
        
        ProjectConfig.RagConfig ragConfig = new ProjectConfig.RagConfig(enabled, branch, excludePatterns);
        
        project.setConfiguration(new ProjectConfig(useLocalMcp, defaultBranch, branchAnalysis, ragConfig));
        return projectRepository.save(project);
    }
}
