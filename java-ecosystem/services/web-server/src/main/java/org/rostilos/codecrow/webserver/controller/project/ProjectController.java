package org.rostilos.codecrow.webserver.controller.project;

import java.security.GeneralSecurityException;
import java.util.List;
import java.util.stream.Collectors;

import org.rostilos.codecrow.core.dto.project.ProjectDTO;
import org.rostilos.codecrow.core.model.project.Project;
import org.rostilos.codecrow.core.model.project.config.ProjectConfig;
import org.rostilos.codecrow.core.model.workspace.Workspace;
import org.rostilos.codecrow.security.service.UserDetailsImpl;
import org.rostilos.codecrow.webserver.dto.request.project.BindAiConnectionRequest;
import org.rostilos.codecrow.webserver.dto.request.project.BindRepositoryRequest;
import org.rostilos.codecrow.webserver.dto.request.project.CreateProjectRequest;
import org.rostilos.codecrow.webserver.dto.request.project.UpdateProjectRequest;
import org.rostilos.codecrow.webserver.dto.request.project.UpdateRepositorySettingsRequest;
import org.rostilos.codecrow.webserver.dto.request.project.CreateProjectTokenRequest;
import org.rostilos.codecrow.webserver.dto.request.project.UpdateRagConfigRequest;
import org.rostilos.codecrow.webserver.dto.project.ProjectTokenDTO;
import org.rostilos.codecrow.webserver.dto.response.project.RagIndexStatusDTO;
import org.rostilos.codecrow.core.dto.message.MessageResponse;
import org.rostilos.codecrow.webserver.service.auth.TwoFactorAuthService;
import org.rostilos.codecrow.webserver.service.project.ProjectService;
import org.rostilos.codecrow.webserver.service.project.ProjectTokenService;
import org.rostilos.codecrow.webserver.service.project.RagIndexStatusService;
import org.rostilos.codecrow.webserver.service.project.RagIndexingTriggerService;
import org.rostilos.codecrow.webserver.service.workspace.WorkspaceService;
import org.springframework.http.HttpStatus;
import org.springframework.http.MediaType;
import org.springframework.http.ResponseEntity;
import org.springframework.security.access.prepost.PreAuthorize;
import org.springframework.security.core.annotation.AuthenticationPrincipal;
import org.springframework.validation.BindingResult;
import org.springframework.validation.FieldError;
import org.springframework.web.bind.annotation.CrossOrigin;
import org.springframework.web.bind.annotation.DeleteMapping;
import org.springframework.web.bind.annotation.GetMapping;
import org.springframework.web.bind.annotation.PatchMapping;
import org.springframework.web.bind.annotation.PathVariable;
import org.springframework.web.bind.annotation.PostMapping;
import org.springframework.web.bind.annotation.PutMapping;
import org.springframework.web.bind.annotation.RequestBody;
import org.springframework.web.bind.annotation.RequestHeader;
import org.springframework.web.bind.annotation.RequestMapping;
import org.springframework.web.bind.annotation.RequestParam;
import org.springframework.web.bind.annotation.RestController;
import org.springframework.web.servlet.mvc.method.annotation.SseEmitter;

import jakarta.validation.Valid;

@CrossOrigin(origins = "*", maxAge = 3600)
@RestController
@RequestMapping("/api/{workspaceSlug}/project")
public class ProjectController {
    private final ProjectService projectService;
    private final ProjectTokenService projectTokenService;
    private final WorkspaceService workspaceService;
    private final RagIndexStatusService ragIndexStatusService;
    private final RagIndexingTriggerService ragIndexingTriggerService;
    private final TwoFactorAuthService twoFactorAuthService;

    public ProjectController(
            ProjectService projectService,
            ProjectTokenService projectTokenService,
            WorkspaceService workspaceService,
            RagIndexStatusService ragIndexStatusService,
            RagIndexingTriggerService ragIndexingTriggerService,
            TwoFactorAuthService twoFactorAuthService
    ) {
        this.projectService = projectService;
        this.projectTokenService = projectTokenService;
        this.workspaceService = workspaceService;
        this.ragIndexStatusService = ragIndexStatusService;
        this.ragIndexingTriggerService = ragIndexingTriggerService;
        this.twoFactorAuthService = twoFactorAuthService;
    }

    @GetMapping("/project_list")
    @PreAuthorize("@workspaceSecurity.isWorkspaceMember(#workspaceSlug, authentication)")
    public ResponseEntity<?> getUserWorkspaceProjectsList(@PathVariable String workspaceSlug) {
        Workspace workspace = workspaceService.getWorkspaceBySlug(workspaceSlug);
        List<Project> userWorkspaceProjects = projectService.listWorkspaceProjects(workspace.getId());

        List<ProjectDTO> projectDTOs = userWorkspaceProjects.stream()
                .map(ProjectDTO::fromProject)
                .collect(Collectors.toList());

        return new ResponseEntity<>(projectDTOs, HttpStatus.OK);
    }

    @PostMapping("/create")
    @PreAuthorize("@workspaceSecurity.hasOwnerOrAdminRights(#workspaceSlug, authentication)")
    public ResponseEntity<?> createProject(
            @PathVariable String workspaceSlug,
            @Valid @RequestBody CreateProjectRequest request,
            BindingResult bindingResult
    ) {
        if (bindingResult.hasErrors()) {
            String errorMessage = bindingResult.getFieldErrors().stream()
                    .map(FieldError::getDefaultMessage)
                    .collect(Collectors.joining(", "));
            return ResponseEntity.badRequest()
                    .body(new MessageResponse("Validation failed: " + errorMessage));
        }

        Workspace workspace = workspaceService.getWorkspaceBySlug(workspaceSlug);
        Project created = projectService.createProject(workspace.getId(), request);
        return new ResponseEntity<>(ProjectDTO.fromProject(created), HttpStatus.CREATED);
    }

    @PostMapping("/{projectNamespace}/token/generate")
    @PreAuthorize("@workspaceSecurity.hasOwnerOrAdminRights(#workspaceSlug, authentication)")
    public ResponseEntity<?> generateProjectJwt(
            @PathVariable String workspaceSlug,
            @PathVariable String projectNamespace,
            @AuthenticationPrincipal UserDetailsImpl userDetails,
            @RequestBody CreateProjectTokenRequest request
    ) throws GeneralSecurityException {
        Workspace workspace = workspaceService.getWorkspaceBySlug(workspaceSlug);
        Project project = projectService.getProjectByWorkspaceAndNamespace(workspace.getId(), projectNamespace);
        String jwt = projectTokenService.generateProjectJwt(
                workspace.getId(),
                project.getId(),
                userDetails.getId(),
                request.getName(),
                request.getLifetime()
        );
        return ResponseEntity.ok(java.util.Map.of("token", jwt));
    }

    @GetMapping("/{projectNamespace}/token")
    @PreAuthorize("@workspaceSecurity.hasOwnerOrAdminRights(#workspaceSlug, authentication)")
    public ResponseEntity<?> listProjectTokens(
            @PathVariable String workspaceSlug,
            @PathVariable String projectNamespace
    ) {
        Workspace workspace = workspaceService.getWorkspaceBySlug(workspaceSlug);
        Project project = projectService.getProjectByWorkspaceAndNamespace(workspace.getId(), projectNamespace);
        var tokens = projectTokenService.listTokens(workspace.getId(), project.getId()).stream()
                .map(ProjectTokenDTO::from)
                .toList();
        return new ResponseEntity<>(tokens, HttpStatus.OK);
    }

    @DeleteMapping("/{projectNamespace}/token/{tokenId}")
    @PreAuthorize("@workspaceSecurity.hasOwnerOrAdminRights(#workspaceSlug, authentication)")
    public ResponseEntity<?> deleteProjectToken(
            @PathVariable String workspaceSlug,
            @PathVariable String projectNamespace,
            @PathVariable Long tokenId
    ) {
        Workspace workspace = workspaceService.getWorkspaceBySlug(workspaceSlug);
        Project project = projectService.getProjectByWorkspaceAndNamespace(workspace.getId(), projectNamespace);
        projectTokenService.deleteToken(workspace.getId(), project.getId(), tokenId);
        return new ResponseEntity<>(HttpStatus.NO_CONTENT);
    }


    //TODO: service implementation, more fields to update
    @PatchMapping("/{projectNamespace}")
    @PreAuthorize("@workspaceSecurity.hasOwnerOrAdminRights(#workspaceSlug, authentication)")
    public ResponseEntity<?> updateProject(
            @PathVariable String workspaceSlug,
            @PathVariable String projectNamespace,
            @RequestBody UpdateProjectRequest request
    ) {
        Workspace workspace = workspaceService.getWorkspaceBySlug(workspaceSlug);
        Project project = projectService.getProjectByWorkspaceAndNamespace(workspace.getId(), projectNamespace);
        Project updated = projectService.updateProject(workspace.getId(), project.getId(), request);
        return new ResponseEntity<>(ProjectDTO.fromProject(updated), HttpStatus.OK);
    }

    @DeleteMapping("/{projectNamespace}")
    @PreAuthorize("@workspaceSecurity.hasOwnerOrAdminRights(#workspaceSlug, authentication)")
    public ResponseEntity<?> deleteProject(
            @PathVariable String workspaceSlug,
            @PathVariable String projectNamespace,
            @RequestHeader(value = "X-2FA-Code", required = false) String twoFactorCode,
            @AuthenticationPrincipal UserDetailsImpl userDetails
    ) {
        // Verify 2FA if enabled for the user (throws exception if verification fails)
        twoFactorAuthService.verifySensitiveOperation(userDetails.getId(), twoFactorCode);
        
        Workspace workspace = workspaceService.getWorkspaceBySlug(workspaceSlug);
        projectService.deleteProjectByNamespace(workspace.getId(), projectNamespace);
        return new ResponseEntity<>(HttpStatus.NO_CONTENT);
    }

    @PutMapping("/{projectNamespace}/repository/bind")
    @PreAuthorize("@workspaceSecurity.hasOwnerOrAdminRights(#workspaceSlug, authentication)")
    public ResponseEntity<?> bindRepository(
            @PathVariable String workspaceSlug,
            @PathVariable String projectNamespace,
            @RequestBody BindRepositoryRequest request
    ) {
        Workspace workspace = workspaceService.getWorkspaceBySlug(workspaceSlug);
        Project project = projectService.getProjectByWorkspaceAndNamespace(workspace.getId(), projectNamespace);
        Project p = projectService.bindRepository(workspace.getId(), project.getId(), request);
        return new ResponseEntity<>(ProjectDTO.fromProject(p), HttpStatus.OK);
    }

    @DeleteMapping("/{projectNamespace}/repository/unbind")
    @PreAuthorize("@workspaceSecurity.hasOwnerOrAdminRights(#workspaceSlug, authentication)")
    public ResponseEntity<?> unbindRepository(
            @PathVariable String workspaceSlug,
            @PathVariable String projectNamespace,
            @RequestHeader(value = "X-2FA-Code", required = false) String twoFactorCode,
            @AuthenticationPrincipal UserDetailsImpl userDetails
    ) {
        // Verify 2FA if enabled for the user (throws exception if verification fails)
        twoFactorAuthService.verifySensitiveOperation(userDetails.getId(), twoFactorCode);
        
        Workspace workspace = workspaceService.getWorkspaceBySlug(workspaceSlug);
        Project project = projectService.getProjectByWorkspaceAndNamespace(workspace.getId(), projectNamespace);
        Project p = projectService.unbindRepository(workspace.getId(), project.getId());
        return new ResponseEntity<>(ProjectDTO.fromProject(p), HttpStatus.OK);
    }

    @PatchMapping("/{projectNamespace}/repository/settings")
    @PreAuthorize("@workspaceSecurity.hasOwnerOrAdminRights(#workspaceSlug, authentication)")
    public ResponseEntity<?> updateRepositorySettings(
            @PathVariable String workspaceSlug,
            @PathVariable String projectNamespace,
            @RequestBody UpdateRepositorySettingsRequest request
    ) throws GeneralSecurityException {
        Workspace workspace = workspaceService.getWorkspaceBySlug(workspaceSlug);
        Project project = projectService.getProjectByWorkspaceAndNamespace(workspace.getId(), projectNamespace);
        projectService.updateRepositorySettings(workspace.getId(), project.getId(), request);
        return new ResponseEntity<>(HttpStatus.NO_CONTENT);
    }

    @PutMapping("/{projectNamespace}/ai/bind")
    @PreAuthorize("@workspaceSecurity.hasOwnerOrAdminRights(#workspaceSlug, authentication)")
    public ResponseEntity<?> bindAiConnection(
            @PathVariable String workspaceSlug,
            @PathVariable String projectNamespace,
            @RequestBody BindAiConnectionRequest request
    ) {
        Workspace workspace = workspaceService.getWorkspaceBySlug(workspaceSlug);
        Project project = projectService.getProjectByWorkspaceAndNamespace(workspace.getId(), projectNamespace);
        boolean status = projectService.bindAiConnection(workspace.getId(), project.getId(), request);
        return new ResponseEntity<>(status ? HttpStatus.NO_CONTENT : HttpStatus.NOT_FOUND);
    }

    /**
     * GET /api/workspace/{workspaceSlug}/project/{projectNamespace}/branches
     * Returns list of analyzed branches for the project
     */
    @GetMapping("/{projectNamespace}/branches")
    @PreAuthorize("@workspaceSecurity.isWorkspaceMember(#workspaceSlug, authentication)")
    public ResponseEntity<?> getProjectBranches(
            @PathVariable String workspaceSlug,
            @PathVariable String projectNamespace
    ) {
        Workspace workspace = workspaceService.getWorkspaceBySlug(workspaceSlug);
        List<org.rostilos.codecrow.core.model.branch.Branch> branches = projectService.getProjectBranches(workspace.getId(), projectNamespace);
        List<BranchDTO> branchDTOs = branches.stream()
                .map(b -> new BranchDTO(
                        b.getId(),
                        b.getBranchName(),
                        b.getCommitHash(),
                        b.getTotalIssues(),
                        b.getHighSeverityCount(),
                        b.getMediumSeverityCount(),
                        b.getLowSeverityCount(),
                        b.getResolvedCount(),
                        b.getUpdatedAt()
                ))
                .collect(Collectors.toList());
        return new ResponseEntity<>(branchDTOs, HttpStatus.OK);
    }

    /**
     * PUT /api/workspace/{workspaceSlug}/project/{projectNamespace}/default-branch
     * Sets the default branch for a project
     * Request body: { "branchId": 123 } or { "branchName": "main" }
     */
    @PutMapping("/{projectNamespace}/default-branch")
    @PreAuthorize("@workspaceSecurity.hasOwnerOrAdminRights(#workspaceSlug, authentication)")
    public ResponseEntity<?> setDefaultBranch(
            @PathVariable String workspaceSlug,
            @PathVariable String projectNamespace,
            @RequestBody SetDefaultBranchRequest request
    ) {
        Workspace workspace = workspaceService.getWorkspaceBySlug(workspaceSlug);
        Project project;
        if (request.branchId() != null) {
            project = projectService.setDefaultBranch(workspace.getId(), projectNamespace, request.branchId());
        } else if (request.branchName() != null && !request.branchName().isBlank()) {
            project = projectService.setDefaultBranchByName(workspace.getId(), projectNamespace, request.branchName());
        } else {
            return ResponseEntity.badRequest()
                    .body(new MessageResponse("Either branchId or branchName must be provided"));
        }
        return new ResponseEntity<>(ProjectDTO.fromProject(project), HttpStatus.OK);
    }

    // DTOs
    public record BranchDTO(
            Long id,
            String branchName,
            String commitHash,
            int totalIssues,
            int highSeverityCount,
            int mediumSeverityCount,
            int lowSeverityCount,
            int resolvedCount,
            java.time.OffsetDateTime updatedAt
    ) {}

    public record SetDefaultBranchRequest(
            Long branchId,
            String branchName
    ) {}

    /**
     * GET /api/workspace/{workspaceSlug}/project/{projectNamespace}/branch-analysis-config
     * Returns the branch analysis configuration for the project
     */
    @GetMapping("/{projectNamespace}/branch-analysis-config")
    @PreAuthorize("@workspaceSecurity.isWorkspaceMember(#workspaceSlug, authentication)")
    public ResponseEntity<?> getBranchAnalysisConfig(
            @PathVariable String workspaceSlug,
            @PathVariable String projectNamespace
    ) {
        Workspace workspace = workspaceService.getWorkspaceBySlug(workspaceSlug);
        Project project = projectService.getProjectByWorkspaceAndNamespace(workspace.getId(), projectNamespace);
        var config = projectService.getBranchAnalysisConfig(project);
        return new ResponseEntity<>(config, HttpStatus.OK);
    }

    /**
     * PUT /api/workspace/{workspaceSlug}/project/{projectNamespace}/branch-analysis-config
     * Updates the branch analysis configuration for the project
     * Request body: { "prTargetBranches": ["main", "develop", "release/*"], "branchPushPatterns": ["main", "develop"] }
     */
    @PutMapping("/{projectNamespace}/branch-analysis-config")
    @PreAuthorize("@workspaceSecurity.hasOwnerOrAdminRights(#workspaceSlug, authentication)")
    public ResponseEntity<?> updateBranchAnalysisConfig(
            @PathVariable String workspaceSlug,
            @PathVariable String projectNamespace,
            @RequestBody BranchAnalysisConfigRequest request
    ) {
        Workspace workspace = workspaceService.getWorkspaceBySlug(workspaceSlug);
        Project project = projectService.getProjectByWorkspaceAndNamespace(workspace.getId(), projectNamespace);
        Project updated = projectService.updateBranchAnalysisConfig(
                workspace.getId(),
                project.getId(),
                request.prTargetBranches(),
                request.branchPushPatterns()
        );
        return new ResponseEntity<>(ProjectDTO.fromProject(updated), HttpStatus.OK);
    }

    public record BranchAnalysisConfigRequest(
            List<String> prTargetBranches,
            List<String> branchPushPatterns
    ) {}

    // ==================== RAG Config Endpoints ====================

    /**
     * GET /api/workspace/{workspaceSlug}/project/{projectNamespace}/rag/status
     * Returns the RAG indexing status for the project
     */
    @GetMapping("/{projectNamespace}/rag/status")
    @PreAuthorize("@workspaceSecurity.isWorkspaceMember(#workspaceSlug, authentication)")
    public ResponseEntity<?> getRagIndexStatus(
            @PathVariable String workspaceSlug,
            @PathVariable String projectNamespace
    ) {
        Workspace workspace = workspaceService.getWorkspaceBySlug(workspaceSlug);
        Project project = projectService.getProjectByWorkspaceAndNamespace(workspace.getId(), projectNamespace);
        var status = ragIndexStatusService.getIndexStatus(project);
        
        if (status.isEmpty()) {
            return new ResponseEntity<>(new RagStatusResponse(
                    false,
                    null,
                    true // canStartIndexing
            ), HttpStatus.OK);
        }
        
        return new ResponseEntity<>(new RagStatusResponse(
                ragIndexStatusService.isProjectIndexed(project.getId()),
                RagIndexStatusDTO.fromEntity(status.get()),
                ragIndexStatusService.canStartIndexing(project)
        ), HttpStatus.OK);
    }

    /**
     * PUT /api/workspace/{workspaceSlug}/project/{projectNamespace}/rag/config
     * Updates the RAG configuration for the project (enable/disable, set branch, exclude patterns)
     */
    @PutMapping("/{projectNamespace}/rag/config")
    @PreAuthorize("@workspaceSecurity.hasOwnerOrAdminRights(#workspaceSlug, authentication)")
    public ResponseEntity<?> updateRagConfig(
            @PathVariable String workspaceSlug,
            @PathVariable String projectNamespace,
            @Valid @RequestBody UpdateRagConfigRequest request
    ) {
        Workspace workspace = workspaceService.getWorkspaceBySlug(workspaceSlug);
        Project project = projectService.getProjectByWorkspaceAndNamespace(workspace.getId(), projectNamespace);
        Project updated = projectService.updateRagConfig(
                workspace.getId(),
                project.getId(),
                request.getEnabled(),
                request.getBranch(),
                request.getExcludePatterns()
        );
        return new ResponseEntity<>(ProjectDTO.fromProject(updated), HttpStatus.OK);
    }

    /**
     * POST /api/workspace/{workspaceSlug}/project/{projectNamespace}/rag/trigger
     * Triggers RAG indexing for the project. Returns an SSE stream with progress updates.
     * 
     * Security:
     * - Requires authenticated user with owner/admin rights on the workspace
     * - Generates a short-lived project JWT for pipeline-agent authentication
     * - Rate-limited to prevent abuse (min 60 seconds between triggers per project)
     */
    @PostMapping(value = "/{projectNamespace}/rag/trigger", produces = MediaType.TEXT_EVENT_STREAM_VALUE)
    @PreAuthorize("@workspaceSecurity.hasOwnerOrAdminRights(#workspaceSlug, authentication)")
    public SseEmitter triggerRagIndexing(
            @PathVariable String workspaceSlug,
            @PathVariable String projectNamespace,
            @RequestParam(required = false) String branch,
            @AuthenticationPrincipal UserDetailsImpl userDetails
    ) {
        Workspace workspace = workspaceService.getWorkspaceBySlug(workspaceSlug);
        Project project = projectService.getProjectByWorkspaceAndNamespace(workspace.getId(), projectNamespace);
        
        // Validate before creating emitter
        var validation = ragIndexingTriggerService.validateTrigger(project.getId(), userDetails.getId());
        
        // Create SSE emitter with 30 minute timeout (long-running indexing operation)
        SseEmitter emitter = new SseEmitter(30 * 60 * 1000L);
        
        // Set up emitter callbacks for proper cleanup
        emitter.onCompletion(() -> {
            // Cleanup if needed
        });
        emitter.onTimeout(() -> {
            emitter.complete();
        });
        emitter.onError((ex) -> {
            emitter.complete();
        });
        
        if (!validation.valid()) {
            // Send error and complete immediately
            try {
                emitter.send(SseEmitter.event()
                        .name("error")
                        .data("{\"type\":\"error\",\"status\":\"error\",\"message\":\"" + 
                              validation.message().replace("\"", "\\\"") + "\"}"));
                emitter.complete();
            } catch (Exception e) {
                emitter.completeWithError(e);
            }
            return emitter;
        }
        
        // Trigger indexing asynchronously
        ragIndexingTriggerService.triggerIndexing(
                workspace.getId(),
                project.getId(),
                userDetails.getId(),
                branch,
                emitter
        );
        
        return emitter;
    }

    public record RagStatusResponse(
            boolean isIndexed,
            RagIndexStatusDTO indexStatus,
            boolean canStartIndexing
    ) {}
    

    /**
     * Updates analysis settings for the project (PR auto-analysis, branch analysis, installation method)
     */
    @PutMapping("/{projectNamespace}/analysis-settings")
    @PreAuthorize("@workspaceSecurity.hasOwnerOrAdminRights(#workspaceSlug, authentication)")
    public ResponseEntity<?> updateAnalysisSettings(
            @PathVariable String workspaceSlug,
            @PathVariable String projectNamespace,
            @Valid @RequestBody UpdateAnalysisSettingsRequest request
    ) {
        Workspace workspace = workspaceService.getWorkspaceBySlug(workspaceSlug);
        Project project = projectService.getProjectByWorkspaceAndNamespace(workspace.getId(), projectNamespace);
        
        ProjectConfig.InstallationMethod installationMethod = null;
        if (request.installationMethod() != null) {
            try {
                installationMethod = ProjectConfig.InstallationMethod.valueOf(request.installationMethod());
            } catch (IllegalArgumentException e) {
                return new ResponseEntity<>("Invalid installation method: " + request.installationMethod(), HttpStatus.BAD_REQUEST);
            }
        }
        
        Project updated = projectService.updateAnalysisSettings(
                workspace.getId(),
                project.getId(),
                request.prAnalysisEnabled(),
                request.branchAnalysisEnabled(),
                installationMethod
        );
        return new ResponseEntity<>(ProjectDTO.fromProject(updated), HttpStatus.OK);
    }
    
    public record UpdateAnalysisSettingsRequest(
            Boolean prAnalysisEnabled,
            Boolean branchAnalysisEnabled,
            String installationMethod
    ) {}
}
