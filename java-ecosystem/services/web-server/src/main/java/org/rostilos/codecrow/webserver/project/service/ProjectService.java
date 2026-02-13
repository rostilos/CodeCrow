package org.rostilos.codecrow.webserver.project.service;

import java.security.GeneralSecurityException;
import java.security.SecureRandom;
import java.util.Base64;
import java.util.List;
import java.util.NoSuchElementException;

import org.rostilos.codecrow.core.model.ai.AIConnection;
import org.rostilos.codecrow.core.model.branch.Branch;
import org.rostilos.codecrow.core.model.project.Project;
import org.rostilos.codecrow.core.model.project.ProjectAiConnectionBinding;
import org.rostilos.codecrow.core.model.qualitygate.QualityGate;
import org.rostilos.codecrow.core.model.vcs.EVcsConnectionType;
import org.rostilos.codecrow.core.model.vcs.EVcsProvider;
import org.rostilos.codecrow.core.model.vcs.VcsConnection;
import org.rostilos.codecrow.core.model.vcs.VcsRepoBinding;
import org.rostilos.codecrow.core.model.vcs.config.cloud.BitbucketCloudConfig;
import org.rostilos.codecrow.core.model.workspace.Workspace;
import org.rostilos.codecrow.core.persistence.repository.ai.AiConnectionRepository;
import org.rostilos.codecrow.core.persistence.repository.analysis.AnalysisLockRepository;
import org.rostilos.codecrow.core.persistence.repository.analysis.RagIndexStatusRepository;
import org.rostilos.codecrow.core.persistence.repository.branch.BranchFileRepository;
import org.rostilos.codecrow.core.persistence.repository.branch.BranchIssueRepository;
import org.rostilos.codecrow.core.persistence.repository.branch.BranchRepository;
import org.rostilos.codecrow.core.persistence.repository.codeanalysis.CodeAnalysisRepository;
import org.rostilos.codecrow.core.persistence.repository.codeanalysis.PrSummarizeCacheRepository;
import org.rostilos.codecrow.core.persistence.repository.job.JobLogRepository;
import org.rostilos.codecrow.core.persistence.repository.job.JobRepository;
import org.rostilos.codecrow.core.persistence.repository.project.ProjectRepository;
import org.rostilos.codecrow.core.persistence.repository.project.ProjectTokenRepository;
import org.rostilos.codecrow.core.persistence.repository.pullrequest.PullRequestRepository;
import org.rostilos.codecrow.core.persistence.repository.vcs.VcsConnectionRepository;
import org.rostilos.codecrow.core.persistence.repository.vcs.VcsRepoBindingRepository;
import org.rostilos.codecrow.core.persistence.repository.workspace.WorkspaceRepository;
import org.rostilos.codecrow.core.persistence.repository.qualitygate.QualityGateRepository;
import org.rostilos.codecrow.core.model.project.config.BranchAnalysisConfig;
import org.rostilos.codecrow.core.model.project.config.CommandAuthorizationMode;
import org.rostilos.codecrow.core.model.project.config.CommentCommandsConfig;
import org.rostilos.codecrow.core.model.project.config.InstallationMethod;
import org.rostilos.codecrow.core.model.project.config.ProjectConfig;
import org.rostilos.codecrow.core.model.project.config.RagConfig;
import org.rostilos.codecrow.security.oauth.TokenEncryptionService;
import org.rostilos.codecrow.vcsclient.VcsClientProvider;
import org.rostilos.codecrow.webserver.project.dto.request.BindAiConnectionRequest;
import org.rostilos.codecrow.webserver.project.dto.request.BindRepositoryRequest;
import org.rostilos.codecrow.webserver.project.dto.request.ChangeVcsConnectionRequest;
import org.rostilos.codecrow.webserver.project.dto.request.CreateProjectRequest;
import org.rostilos.codecrow.webserver.project.dto.request.UpdateProjectRequest;
import org.rostilos.codecrow.webserver.project.dto.request.UpdateRepositorySettingsRequest;
import org.rostilos.codecrow.webserver.exception.InvalidProjectRequestException;
import org.rostilos.codecrow.core.service.SiteSettingsProvider;
import org.springframework.stereotype.Service;
import org.springframework.transaction.annotation.Transactional;

import io.jsonwebtoken.security.SecurityException;
import org.slf4j.Logger;
import org.slf4j.LoggerFactory;

@Service
public class ProjectService implements IProjectService {
    private static final Logger log = LoggerFactory.getLogger(ProjectService.class);

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
    private final AnalysisLockRepository analysisLockRepository;
    private final RagIndexStatusRepository ragIndexStatusRepository;
    private final JobRepository jobRepository;
    private final JobLogRepository jobLogRepository;
    private final PrSummarizeCacheRepository prSummarizeCacheRepository;
    private final VcsClientProvider vcsClientProvider;
    private final QualityGateRepository qualityGateRepository;
    private final SiteSettingsProvider siteSettingsProvider;

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
            AnalysisLockRepository analysisLockRepository,
            RagIndexStatusRepository ragIndexStatusRepository,
            JobRepository jobRepository,
            JobLogRepository jobLogRepository,
            PrSummarizeCacheRepository prSummarizeCacheRepository,
            VcsClientProvider vcsClientProvider,
            QualityGateRepository qualityGateRepository,
            SiteSettingsProvider siteSettingsProvider) {
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
        this.analysisLockRepository = analysisLockRepository;
        this.ragIndexStatusRepository = ragIndexStatusRepository;
        this.jobRepository = jobRepository;
        this.jobLogRepository = jobLogRepository;
        this.prSummarizeCacheRepository = prSummarizeCacheRepository;
        this.vcsClientProvider = vcsClientProvider;
        this.qualityGateRepository = qualityGateRepository;
        this.siteSettingsProvider = siteSettingsProvider;
    }

    @Transactional(readOnly = true)
    public List<Project> listWorkspaceProjects(Long workspaceId) {
        // Use the method that fetches default branch eagerly to include stats in
        // project list
        return projectRepository.findByWorkspaceIdWithDefaultBranch(workspaceId);
    }

    @Transactional(readOnly = true)
    public org.springframework.data.domain.Page<Project> listWorkspaceProjectsPaginated(
            Long workspaceId,
            String search,
            int page,
            int size) {
        org.springframework.data.domain.Pageable pageable = org.springframework.data.domain.PageRequest.of(
                page,
                size,
                org.springframework.data.domain.Sort.by(org.springframework.data.domain.Sort.Direction.DESC, "id"));
        return projectRepository.findByWorkspaceIdWithSearchPaginated(workspaceId, search, pageable);
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
                .ifPresent(p -> {
                    throw new InvalidProjectRequestException("Project namespace already exists in workspace");
                });

        Project newProject = new Project();
        newProject.setWorkspace(ws);
        newProject.setName(request.getName());
        newProject.setNamespace(request.getNamespace());
        newProject.setDescription(request.getDescription());
        newProject.setIsActive(true);

        // persist main branch into project configuration (if provided)
        String mainBranch = null;
        if (request.getMainBranch() != null && !request.getMainBranch().isBlank()) {
            mainBranch = request.getMainBranch();
        }
        ProjectConfig config = new ProjectConfig(false, mainBranch);
        // Ensure main branch is always included in analysis patterns
        config.ensureMainBranchInPatterns();
        newProject.setConfiguration(config);

        if (request.hasVcsConnection()) {
            VcsConnection vcsConnection = vcsConnectionRepository
                    .findByWorkspace_IdAndId(workspaceId, request.getVcsConnectionId())
                    .orElseThrow(() -> new NoSuchElementException("VCS connection not found!"));

            // Use VcsRepoBinding (provider-agnostic) instead of legacy
            // ProjectVcsConnectionBinding
            VcsRepoBinding vcsRepoBinding = new VcsRepoBinding();
            vcsRepoBinding.setProject(newProject);
            vcsRepoBinding.setWorkspace(ws);
            vcsRepoBinding.setProvider(request.getVcsProvider());
            vcsRepoBinding.setVcsConnection(vcsConnection);
            vcsRepoBinding.setExternalRepoSlug(request.getRepositorySlug());
            // Store UUID as string for provider-agnostic storage
            if (request.getRepositoryUUID() != null) {
                vcsRepoBinding.setExternalRepoId(request.getRepositoryUUID().toString());
            }
            // For Bitbucket, extract workspace from config; for other providers, use a
            // default
            String externalNamespace = null;
            if (vcsConnection.getConfiguration() instanceof BitbucketCloudConfig bbConfig) {
                externalNamespace = bbConfig.workspaceId();
            } else if (vcsConnection.getExternalWorkspaceSlug() != null) {
                externalNamespace = vcsConnection.getExternalWorkspaceSlug();
            }
            vcsRepoBinding.setExternalNamespace(externalNamespace);
            vcsRepoBinding.setDefaultBranch(mainBranch);
            newProject.setVcsRepoBinding(vcsRepoBinding);
        }

        if (request.getAiConnectionId() != null) {
            AIConnection aiConnection = aiConnectionRepository
                    .findByWorkspace_IdAndId(workspaceId, request.getAiConnectionId())
                    .orElseThrow(() -> new NoSuchElementException("AI connection not found!"));

            ProjectAiConnectionBinding aiBinding = new ProjectAiConnectionBinding();
            aiBinding.setProject(newProject);
            aiBinding.setAiConnection(aiConnection);
            newProject.setAiConnectionBinding(aiBinding);
        }

        // generate internal auth token for the project
        try {
            byte[] random = new byte[32];
            new SecureRandom().nextBytes(random);
            String plainToken = Base64.getUrlEncoder().withoutPadding().encodeToString(random);
            String encrypted = tokenEncryptionService.encrypt(plainToken);
            newProject.setAuthToken(encrypted);
            // Note: plainToken is not returned in API responses; store encrypted token
            // only.
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
        // Job logs must be deleted before jobs (job_log references job)
        // Jobs must be deleted before codeAnalysis (job references analysis)
        jobLogRepository.deleteByProjectId(projectId);
        jobRepository.deleteByProjectId(projectId);
        branchIssueRepository.deleteByProjectId(projectId);
        codeAnalysisRepository.deleteByProjectId(projectId);
        branchFileRepository.deleteByProjectId(projectId);
        branchRepository.deleteByProjectId(projectId);
        pullRequestRepository.deleteByProject_Id(projectId);
        projectTokenRepository.deleteByProject_Id(projectId);
        vcsRepoBindingRepository.deleteByProject_Id(projectId);
        analysisLockRepository.deleteByProjectId(projectId);
        ragIndexStatusRepository.deleteByProjectId(projectId);
        prSummarizeCacheRepository.deleteByProjectId(projectId);

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

        // Update main branch in project configuration if provided
        if (request.getMainBranch() != null) {
            var cfg = project.getConfiguration();
            if (cfg == null) {
                cfg = new ProjectConfig(false, request.getMainBranch());
            } else {
                cfg.setMainBranch(request.getMainBranch());
            }
            cfg.ensureMainBranchInPatterns();
            project.setConfiguration(cfg);
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
        // Job logs must be deleted before jobs (job_log references job)
        // Jobs must be deleted before codeAnalysis (job references analysis)
        jobLogRepository.deleteByProjectId(projectId);
        jobRepository.deleteByProjectId(projectId);
        branchIssueRepository.deleteByProjectId(projectId);
        codeAnalysisRepository.deleteByProjectId(projectId);
        branchFileRepository.deleteByProjectId(projectId);
        branchRepository.deleteByProjectId(projectId);
        pullRequestRepository.deleteByProject_Id(projectId);
        projectTokenRepository.deleteByProject_Id(projectId);
        vcsRepoBindingRepository.deleteByProject_Id(projectId);
        analysisLockRepository.deleteByProjectId(projectId);
        ragIndexStatusRepository.deleteByProjectId(projectId);
        prSummarizeCacheRepository.deleteByProjectId(projectId);

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
        // TODO: bind implementation
        return projectRepository.save(p);
    }

    @Transactional
    public Project unbindRepository(Long workspaceId, Long projectId) {
        Project p = projectRepository.findByWorkspaceIdAndId(workspaceId, projectId)
                .orElseThrow(() -> new NoSuchElementException("Project not found"));
        // TODO: unbind implementation
        // Clear settings placeholder if used in future
        return projectRepository.save(p);
    }

    @Transactional
    public void updateRepositorySettings(Long workspaceId, Long projectId, UpdateRepositorySettingsRequest request)
            throws GeneralSecurityException {
        Project p = projectRepository.findByWorkspaceIdAndId(workspaceId, projectId)
                .orElseThrow(() -> new NoSuchElementException("Project not found"));

        if (request.getToken() != null && !request.getToken().isBlank()) {
            String encrypted = tokenEncryptionService.encrypt(request.getToken());
            p.setAuthToken(encrypted);
        }
        // TODO: service implementation
        // apiBaseUrl and webhookSecret ignored for now (no fields yet)
        projectRepository.save(p);
    }

    @Transactional
    public boolean bindAiConnection(Long workspaceId, Long projectId, BindAiConnectionRequest request)
            throws SecurityException {
        // Use findByIdWithConnections to eagerly fetch aiBinding for proper orphan
        // removal
        Project project = projectRepository.findByIdWithConnections(projectId)
                .orElseThrow(() -> new NoSuchElementException("Project not found"));

        // Verify workspace ownership
        if (!project.getWorkspace().getId().equals(workspaceId)) {
            throw new NoSuchElementException("Project not found in workspace");
        }

        if (request.getAiConnectionId() != null) {
            Long aiConnectionId = request.getAiConnectionId();
            AIConnection aiConnection = aiConnectionRepository.findByWorkspace_IdAndId(workspaceId, aiConnectionId)
                    .orElseThrow(() -> new NoSuchElementException("Ai connection not found"));

            // Check if there's an existing binding that needs to be updated
            ProjectAiConnectionBinding existingBinding = project.getAiBinding();
            if (existingBinding != null) {
                // Update existing binding instead of creating new one
                existingBinding.setAiConnection(aiConnection);
            } else {
                // Create new binding
                ProjectAiConnectionBinding aiConnectionBinding = new ProjectAiConnectionBinding();
                aiConnectionBinding.setProject(project);
                aiConnectionBinding.setAiConnection(aiConnection);
                project.setAiConnectionBinding(aiConnectionBinding);
            }

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
                .orElseThrow(
                        () -> new NoSuchElementException("Branch '" + branchName + "' not found for this project"));

        project.setDefaultBranch(branch);
        return projectRepository.save(project);
    }

    /**
     * Get the branch analysis configuration for a project.
     * Returns a BranchAnalysisConfig record or null if not configured.
     */
    @Transactional(readOnly = true)
    public BranchAnalysisConfig getBranchAnalysisConfig(Project project) {
        if (project.getConfiguration() == null) {
            return null;
        }
        return project.getConfiguration().branchAnalysis();
    }

    /**
     * Update the branch analysis configuration for a project.
     * Main branch is always ensured to be in the patterns.
     * 
     * @param workspaceId        the workspace ID
     * @param projectId          the project ID
     * @param prTargetBranches   patterns for PR target branches (e.g., ["main",
     *                           "develop", "release/*"])
     * @param branchPushPatterns patterns for branch push analysis (e.g., ["main",
     *                           "develop"])
     * @return the updated project
     */
    @Transactional
    public Project updateBranchAnalysisConfig(
            Long workspaceId,
            Long projectId,
            List<String> prTargetBranches,
            List<String> branchPushPatterns) {
        Project project = projectRepository.findByWorkspaceIdAndId(workspaceId, projectId)
                .orElseThrow(() -> new NoSuchElementException("Project not found"));

        ProjectConfig currentConfig = project.getConfiguration();
        if (currentConfig == null) {
            currentConfig = new ProjectConfig(false, null);
        }

        BranchAnalysisConfig branchConfig = new BranchAnalysisConfig(
                prTargetBranches,
                branchPushPatterns);

        currentConfig.setBranchAnalysis(branchConfig);
        // Ensure main branch is always included in patterns
        currentConfig.ensureMainBranchInPatterns();
        project.setConfiguration(currentConfig);
        return projectRepository.save(project);
    }

    /**
     * Update the RAG configuration for a project.
     * 
     * @param workspaceId         the workspace ID
     * @param projectId           the project ID
     * @param enabled             whether RAG indexing is enabled
     * @param branch              the branch to index (null uses defaultBranch or
     *                            'main')
     * @param includePatterns     patterns to include in indexing (applied before exclusion)
     * @param excludePatterns     patterns to exclude from indexing
     * @param multiBranchEnabled  whether multi-branch indexing is enabled
     * @param branchRetentionDays how long to keep branch index metadata
     * @return the updated project
     */
    @Transactional
    public Project updateRagConfig(
            Long workspaceId,
            Long projectId,
            boolean enabled,
            String branch,
            java.util.List<String> includePatterns,
            java.util.List<String> excludePatterns,
            Boolean multiBranchEnabled,
            Integer branchRetentionDays) {
        Project project = projectRepository.findByWorkspaceIdAndId(workspaceId, projectId)
                .orElseThrow(() -> new NoSuchElementException("Project not found"));

        ProjectConfig currentConfig = project.getConfiguration();
        boolean useLocalMcp = currentConfig != null && currentConfig.useLocalMcp();
        String mainBranch = currentConfig != null ? currentConfig.mainBranch() : null;
        var branchAnalysis = currentConfig != null ? currentConfig.branchAnalysis() : null;
        Boolean prAnalysisEnabled = currentConfig != null ? currentConfig.prAnalysisEnabled() : true;
        Boolean branchAnalysisEnabled = currentConfig != null ? currentConfig.branchAnalysisEnabled() : true;
        var installationMethod = currentConfig != null ? currentConfig.installationMethod() : null;
        var commentCommands = currentConfig != null ? currentConfig.commentCommands() : null;

        RagConfig ragConfig = new RagConfig(
                enabled, branch, includePatterns, excludePatterns, multiBranchEnabled, branchRetentionDays);

        project.setConfiguration(new ProjectConfig(useLocalMcp, mainBranch, branchAnalysis, ragConfig,
                prAnalysisEnabled, branchAnalysisEnabled, installationMethod, commentCommands));
        return projectRepository.save(project);
    }

    /**
     * Simplified RAG config update (backward compatible).
     */
    @Transactional
    public Project updateRagConfig(
            Long workspaceId,
            Long projectId,
            boolean enabled,
            String branch,
            java.util.List<String> includePatterns,
              java.util.List<String> excludePatterns) {
        return updateRagConfig(workspaceId, projectId, enabled, branch, includePatterns, excludePatterns, null, null);
    }

    @Transactional
    public Project updateAnalysisSettings(
            Long workspaceId,
            Long projectId,
            Boolean prAnalysisEnabled,
            Boolean branchAnalysisEnabled,
            InstallationMethod installationMethod,
            Integer maxAnalysisTokenLimit) {
        Project project = projectRepository.findByWorkspaceIdAndId(workspaceId, projectId)
                .orElseThrow(() -> new NoSuchElementException("Project not found"));

        ProjectConfig currentConfig = project.getConfiguration();
        boolean useLocalMcp = currentConfig != null && currentConfig.useLocalMcp();
        String mainBranch = currentConfig != null ? currentConfig.mainBranch() : null;
        var branchAnalysis = currentConfig != null ? currentConfig.branchAnalysis() : null;
        var ragConfig = currentConfig != null ? currentConfig.ragConfig() : null;
        var commentCommands = currentConfig != null ? currentConfig.commentCommands() : null;
        int currentMaxTokenLimit = currentConfig != null ? currentConfig.maxAnalysisTokenLimit()
                : ProjectConfig.DEFAULT_MAX_ANALYSIS_TOKEN_LIMIT;

        Boolean newPrAnalysis = prAnalysisEnabled != null ? prAnalysisEnabled
                : (currentConfig != null ? currentConfig.prAnalysisEnabled() : true);
        Boolean newBranchAnalysis = branchAnalysisEnabled != null ? branchAnalysisEnabled
                : (currentConfig != null ? currentConfig.branchAnalysisEnabled() : true);
        var newInstallationMethod = installationMethod != null ? installationMethod
                : (currentConfig != null ? currentConfig.installationMethod() : null);
        int newMaxTokenLimit = maxAnalysisTokenLimit != null ? maxAnalysisTokenLimit : currentMaxTokenLimit;

        // Update both the direct column and the JSON config
        // TODO: remove duplication
        project.setPrAnalysisEnabled(newPrAnalysis != null ? newPrAnalysis : true);
        project.setBranchAnalysisEnabled(newBranchAnalysis != null ? newBranchAnalysis : true);

        project.setConfiguration(new ProjectConfig(useLocalMcp, mainBranch, branchAnalysis, ragConfig,
                newPrAnalysis, newBranchAnalysis, newInstallationMethod, commentCommands, newMaxTokenLimit));
        return projectRepository.save(project);
    }

    /**
     * Update the quality gate for a project.
     * 
     * @param workspaceId   the workspace ID
     * @param projectId     the project ID
     * @param qualityGateId the quality gate ID (null to remove)
     * @return the updated project
     */
    @Transactional
    public Project updateProjectQualityGate(Long workspaceId, Long projectId, Long qualityGateId) {
        Project project = projectRepository.findByWorkspaceIdAndId(workspaceId, projectId)
                .orElseThrow(() -> new NoSuchElementException("Project not found"));

        if (qualityGateId != null) {
            QualityGate qualityGate = qualityGateRepository.findByIdAndWorkspaceId(qualityGateId, workspaceId)
                    .orElseThrow(() -> new NoSuchElementException("Quality gate not found"));
            project.setQualityGate(qualityGate);
        } else {
            project.setQualityGate(null);
        }

        return projectRepository.save(project);
    }

    /**
     * Get the comment commands configuration for a project.
     * Returns a CommentCommandsConfig record (never null, returns default disabled
     * config if not configured).
     */
    @Transactional(readOnly = true)
    public CommentCommandsConfig getCommentCommandsConfig(Project project) {
        if (project.getConfiguration() == null) {
            return new CommentCommandsConfig();
        }
        return project.getConfiguration().getCommentCommandsConfig();
    }

    /**
     * Update the comment commands configuration for a project.
     * 
     * @param workspaceId the workspace ID
     * @param projectId   the project ID
     * @param request     the update request
     * @return the updated project
     */
    @Transactional
    public Project updateCommentCommandsConfig(
            Long workspaceId,
            Long projectId,
            org.rostilos.codecrow.webserver.project.dto.request.UpdateCommentCommandsConfigRequest request) {
        Project project = projectRepository.findByWorkspaceIdAndId(workspaceId, projectId)
                .orElseThrow(() -> new NoSuchElementException("Project not found"));

        ProjectConfig currentConfig = project.getConfiguration();
        boolean useLocalMcp = currentConfig != null && currentConfig.useLocalMcp();
        String mainBranch = currentConfig != null ? currentConfig.mainBranch() : null;
        var branchAnalysis = currentConfig != null ? currentConfig.branchAnalysis() : null;
        var ragConfig = currentConfig != null ? currentConfig.ragConfig() : null;
        Boolean prAnalysisEnabled = currentConfig != null ? currentConfig.prAnalysisEnabled() : true;
        Boolean branchAnalysisEnabled = currentConfig != null ? currentConfig.branchAnalysisEnabled() : true;
        var installationMethod = currentConfig != null ? currentConfig.installationMethod() : null;

        // Build new comment commands config
        var existingCommentConfig = currentConfig != null ? currentConfig.commentCommands() : null;

        boolean enabled = request.enabled() != null ? request.enabled()
                : (existingCommentConfig != null ? existingCommentConfig.enabled() : false);
        Integer rateLimit = request.rateLimit() != null ? request.rateLimit()
                : (existingCommentConfig != null ? existingCommentConfig.rateLimit()
                        : CommentCommandsConfig.DEFAULT_RATE_LIMIT);
        Integer rateLimitWindow = request.rateLimitWindowMinutes() != null ? request.rateLimitWindowMinutes()
                : (existingCommentConfig != null ? existingCommentConfig.rateLimitWindowMinutes()
                        : CommentCommandsConfig.DEFAULT_RATE_LIMIT_WINDOW_MINUTES);
        Boolean allowPublicRepoCommands = request.allowPublicRepoCommands() != null ? request.allowPublicRepoCommands()
                : (existingCommentConfig != null ? existingCommentConfig.allowPublicRepoCommands() : false);
        List<String> allowedCommands = request.allowedCommands() != null ? request.validatedAllowedCommands()
                : (existingCommentConfig != null ? existingCommentConfig.allowedCommands() : null);
        CommandAuthorizationMode authorizationMode = request.authorizationMode() != null ? request.authorizationMode()
                : (existingCommentConfig != null ? existingCommentConfig.authorizationMode()
                        : CommentCommandsConfig.DEFAULT_AUTHORIZATION_MODE);
        Boolean allowPrAuthor = request.allowPrAuthor() != null ? request.allowPrAuthor()
                : (existingCommentConfig != null ? existingCommentConfig.allowPrAuthor() : true);

        var commentCommands = new CommentCommandsConfig(
                enabled, rateLimit, rateLimitWindow, allowPublicRepoCommands, allowedCommands,
                authorizationMode, allowPrAuthor);

        project.setConfiguration(new ProjectConfig(useLocalMcp, mainBranch, branchAnalysis, ragConfig,
                prAnalysisEnabled, branchAnalysisEnabled, installationMethod, commentCommands));
        return projectRepository.save(project);
    }

    // ==================== Webhook Management ====================

    /**
     * Setup webhooks for a project's bound repository.
     * This is useful when:
     * - Moving from one repository to another
     * - Switching VCS connection types
     * - Webhook was accidentally deleted in the VCS provider
     */
    @Transactional
    public WebhookSetupResult setupWebhooks(Long workspaceId, Long projectId) {
        Project project = projectRepository.findByWorkspaceIdAndId(workspaceId, projectId)
                .orElseThrow(() -> new NoSuchElementException("Project not found"));

        VcsRepoBinding binding = vcsRepoBindingRepository.findByProject_Id(projectId)
                .orElse(null);

        if (binding == null) {
            return new WebhookSetupResult(false, null, null, "No repository binding found for this project");
        }

        VcsConnection connection = binding.getVcsConnection();
        if (connection == null) {
            return new WebhookSetupResult(false, null, null, "No VCS connection found for this project");
        }

        // Generate webhook URL
        String webhookUrl = generateWebhookUrl(binding.getProvider(), project);

        // Ensure auth token exists
        if (project.getAuthToken() == null || project.getAuthToken().isBlank()) {
            byte[] randomBytes = new byte[32];
            new SecureRandom().nextBytes(randomBytes);
            String authToken = Base64.getUrlEncoder().withoutPadding().encodeToString(randomBytes);
            project.setAuthToken(authToken);
            projectRepository.save(project);
        }

        try {
            // Get VCS client and setup webhook
            org.rostilos.codecrow.vcsclient.VcsClient client = vcsClientProvider.getClient(connection);
            List<String> events = getWebhookEvents(binding.getProvider());

            String workspaceIdOrNamespace;
            String repoSlug;

            // For REPOSITORY_TOKEN connections, use the repositoryPath from the connection
            // because the token is scoped to that specific repository
            if (connection.getConnectionType() == EVcsConnectionType.REPOSITORY_TOKEN
                    && connection.getRepositoryPath() != null
                    && !connection.getRepositoryPath().isBlank()) {
                String repositoryPath = connection.getRepositoryPath();
                int lastSlash = repositoryPath.lastIndexOf('/');
                if (lastSlash > 0) {
                    workspaceIdOrNamespace = repositoryPath.substring(0, lastSlash);
                    repoSlug = repositoryPath.substring(lastSlash + 1);
                } else {
                    workspaceIdOrNamespace = binding.getExternalNamespace();
                    repoSlug = repositoryPath;
                }
                log.info("REPOSITORY_TOKEN webhook setup - using repositoryPath: {}, namespace: {}, slug: {}",
                        repositoryPath, workspaceIdOrNamespace, repoSlug);
            } else {
                workspaceIdOrNamespace = binding.getExternalNamespace();
                repoSlug = binding.getExternalRepoSlug();
                log.info("Standard webhook setup - namespace: {}, slug: {}", workspaceIdOrNamespace, repoSlug);
            }

            String webhookId = client.ensureWebhook(workspaceIdOrNamespace, repoSlug, webhookUrl, events);

            if (webhookId != null) {
                binding.setWebhookId(webhookId);
                binding.setWebhooksConfigured(true);
                vcsRepoBindingRepository.save(binding);
                return new WebhookSetupResult(true, webhookId, webhookUrl, "Webhook configured successfully");
            } else {
                return new WebhookSetupResult(false, null, webhookUrl, "Failed to create webhook");
            }
        } catch (Exception e) {
            return new WebhookSetupResult(false, null, webhookUrl, "Error setting up webhook: " + e.getMessage());
        }
    }

    /**
     * Get webhook information for a project.
     */
    @Transactional(readOnly = true)
    public WebhookInfo getWebhookInfo(Long workspaceId, Long projectId) {
        Project project = projectRepository.findByWorkspaceIdAndId(workspaceId, projectId)
                .orElseThrow(() -> new NoSuchElementException("Project not found"));

        VcsRepoBinding binding = vcsRepoBindingRepository.findByProject_Id(projectId)
                .orElse(null);

        if (binding == null) {
            return new WebhookInfo(false, null, null, null);
        }

        String webhookUrl = generateWebhookUrl(binding.getProvider(), project);
        return new WebhookInfo(
                binding.isWebhooksConfigured(),
                binding.getWebhookId(),
                webhookUrl,
                binding.getProvider());
    }

    private String generateWebhookUrl(EVcsProvider provider, Project project) {
        var urls = siteSettingsProvider.getBaseUrlSettings();
        String base = (urls.webhookBaseUrl() != null && !urls.webhookBaseUrl().isBlank())
                ? urls.webhookBaseUrl() : urls.baseUrl();
        return base + "/api/webhooks/" + provider.getId() + "/" + project.getAuthToken();
    }

    private List<String> getWebhookEvents(EVcsProvider provider) {
        return switch (provider) {
            case BITBUCKET_CLOUD -> List.of("pullrequest:created", "pullrequest:updated", "pullrequest:fulfilled",
                    "pullrequest:comment_created", "repo:push");
            case GITHUB -> List.of("pull_request", "pull_request_review_comment", "issue_comment", "push");
            case GITLAB -> List.of("merge_requests_events", "note_events", "push_events");
            default -> List.of();
        };
    }

    // ==================== Change VCS Connection ====================

    /**
     * Change the VCS connection for a project.
     * This will update the VCS binding and optionally setup webhooks.
     * 
     * WARNING: Changing VCS connection may require manual cleanup of old webhooks
     * in the previous repository.
     */
    @Transactional
    public Project changeVcsConnection(Long workspaceId, Long projectId, ChangeVcsConnectionRequest request) {
        Project project = projectRepository.findByWorkspaceIdAndId(workspaceId, projectId)
                .orElseThrow(() -> new NoSuchElementException("Project not found"));

        Workspace workspace = workspaceRepository.findById(workspaceId)
                .orElseThrow(() -> new NoSuchElementException("Workspace not found"));

        VcsConnection newConnection = vcsConnectionRepository
                .findByWorkspace_IdAndId(workspaceId, request.getConnectionId())
                .orElseThrow(() -> new NoSuchElementException("VCS Connection not found"));

        // Get or create VcsRepoBinding
        VcsRepoBinding binding = vcsRepoBindingRepository.findByProject_Id(projectId)
                .orElse(null);

        boolean isNewBinding = (binding == null);
        if (isNewBinding) {
            binding = new VcsRepoBinding();
            binding.setProject(project);
            binding.setWorkspace(workspace);
        }

        // Clear analysis history if requested
        if (request.isClearAnalysisHistory()) {
            clearProjectAnalysisData(projectId);
        }

        // Update binding with new connection info
        binding.setVcsConnection(newConnection);
        binding.setProvider(newConnection.getProviderType());
        binding.setExternalRepoSlug(request.getRepositorySlug());
        binding.setExternalNamespace(
                request.getWorkspaceId() != null ? request.getWorkspaceId() : newConnection.getExternalWorkspaceSlug());
        binding.setExternalRepoId(
                request.getRepositoryId() != null ? request.getRepositoryId() : request.getRepositorySlug());

        if (request.getDefaultBranch() != null && !request.getDefaultBranch().isBlank()) {
            binding.setDefaultBranch(request.getDefaultBranch());
        }

        // Reset webhook status - will be set up fresh
        binding.setWebhooksConfigured(false);
        binding.setWebhookId(null);

        vcsRepoBindingRepository.save(binding);

        // Setup webhooks if requested
        if (request.isSetupWebhooks()) {
            WebhookSetupResult webhookResult = setupWebhooks(workspaceId, projectId);
            // Log the result but don't fail the whole operation
            if (!webhookResult.success()) {
                // Webhook setup failed but connection change succeeded
                // The user can retry webhook setup later
            }
        }

        return projectRepository.findByWorkspaceIdAndId(workspaceId, projectId)
                .orElseThrow(() -> new NoSuchElementException("Project not found after update"));
    }

    /**
     * Clear all analysis data for a project (used when changing repositories).
     */
    private void clearProjectAnalysisData(Long projectId) {
        // Delete in reverse dependency order
        jobLogRepository.deleteByProjectId(projectId);
        jobRepository.deleteByProjectId(projectId);
        prSummarizeCacheRepository.deleteByProjectId(projectId);
        codeAnalysisRepository.deleteByProjectId(projectId);
        branchIssueRepository.deleteByProjectId(projectId);
        branchFileRepository.deleteByProjectId(projectId);
        branchRepository.deleteByProjectId(projectId);
        pullRequestRepository.deleteByProject_Id(projectId);
        analysisLockRepository.deleteByProjectId(projectId);
        ragIndexStatusRepository.deleteByProjectId(projectId);
    }
}
