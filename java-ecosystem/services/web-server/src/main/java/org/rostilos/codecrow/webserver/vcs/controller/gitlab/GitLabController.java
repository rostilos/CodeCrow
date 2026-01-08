package org.rostilos.codecrow.webserver.vcs.controller.gitlab;

import java.io.IOException;
import java.util.List;

import org.rostilos.codecrow.core.dto.gitlab.GitLabDTO;
import org.rostilos.codecrow.core.model.vcs.EVcsProvider;
import org.rostilos.codecrow.core.model.vcs.VcsConnection;
import org.rostilos.codecrow.core.model.vcs.config.gitlab.GitLabConfig;
import org.rostilos.codecrow.core.persistence.repository.vcs.VcsConnectionRepository;
import org.rostilos.codecrow.vcsclient.gitlab.dto.response.RepositorySearchResult;
import org.rostilos.codecrow.webserver.vcs.dto.request.gitlab.GitLabCreateRequest;
import org.rostilos.codecrow.webserver.vcs.dto.request.gitlab.GitLabRepositoryTokenRequest;
import org.rostilos.codecrow.webserver.vcs.service.VcsConnectionWebService;
import org.rostilos.codecrow.webserver.workspace.service.WorkspaceService;
import org.springframework.http.HttpStatus;
import org.springframework.http.ResponseEntity;
import org.springframework.security.access.prepost.PreAuthorize;
import org.springframework.web.bind.annotation.CrossOrigin;
import org.springframework.web.bind.annotation.DeleteMapping;
import org.springframework.web.bind.annotation.GetMapping;
import org.springframework.web.bind.annotation.PatchMapping;
import org.springframework.web.bind.annotation.PathVariable;
import org.springframework.web.bind.annotation.PostMapping;
import org.springframework.web.bind.annotation.RequestBody;
import org.springframework.web.bind.annotation.RequestMapping;
import org.springframework.web.bind.annotation.RequestParam;
import org.springframework.web.bind.annotation.RestController;

@CrossOrigin(origins = "*", maxAge = 3600)
@RestController
@RequestMapping("/api/{workspaceSlug}/vcs/gitlab")
public class GitLabController {
    
    private final VcsConnectionRepository vcsConnectionRepository;
    private final VcsConnectionWebService vcsConnectionService;
    private final WorkspaceService workspaceService;

    public GitLabController(
            VcsConnectionRepository vcsConnectionRepository,
            VcsConnectionWebService vcsConnectionService,
            WorkspaceService workspaceService
    ) {
        this.vcsConnectionRepository = vcsConnectionRepository;
        this.vcsConnectionService = vcsConnectionService;
        this.workspaceService = workspaceService;
    }

    @GetMapping("/list")
    @PreAuthorize("@workspaceSecurity.hasOwnerOrAdminRights(#workspaceSlug, authentication)")
    public ResponseEntity<List<GitLabDTO>> getGitLabConnections(@PathVariable String workspaceSlug) {
        Long workspaceId = workspaceService.getWorkspaceBySlug(workspaceSlug).getId();
        List<VcsConnection> connections = vcsConnectionService.getWorkspaceGitLabConnections(workspaceId);
        List<GitLabDTO> connectionDTOs = connections.stream()
                .map(GitLabDTO::fromVcsConnection)
                .toList();

        return new ResponseEntity<>(connectionDTOs, HttpStatus.OK);
    }

    @PostMapping("/create")
    @PreAuthorize("@workspaceSecurity.hasOwnerOrAdminRights(#workspaceSlug, authentication)")
    public ResponseEntity<GitLabDTO> createGitLabConnection(
            @PathVariable String workspaceSlug,
            @RequestBody GitLabCreateRequest request
    ) {
        Long workspaceId = workspaceService.getWorkspaceBySlug(workspaceSlug).getId();

        GitLabConfig config = new GitLabConfig(
                request.getAccessToken(),
                request.getGroupId(),
                null
        );

        VcsConnection createdConnection = vcsConnectionService.createGitLabConnection(
                workspaceId,
                config,
                request.getConnectionName()
        );

        return new ResponseEntity<>(GitLabDTO.fromVcsConnection(createdConnection), HttpStatus.CREATED);
    }

    @PatchMapping("/connections/{connectionId}")
    @PreAuthorize("@workspaceSecurity.hasOwnerOrAdminRights(#workspaceSlug, authentication)")
    public ResponseEntity<GitLabDTO> updateGitLabConnection(
            @PathVariable String workspaceSlug,
            @PathVariable Long connectionId,
            @RequestBody GitLabCreateRequest request
    ) {
        Long workspaceId = workspaceService.getWorkspaceBySlug(workspaceSlug).getId();
        VcsConnection updatedConnection = vcsConnectionService.updateGitLabConnection(
                workspaceId,
                connectionId,
                request
        );
        return new ResponseEntity<>(GitLabDTO.fromVcsConnection(updatedConnection), HttpStatus.OK);
    }

    @DeleteMapping("/connections/{connectionId}")
    @PreAuthorize("@workspaceSecurity.hasOwnerOrAdminRights(#workspaceSlug, authentication)")
    public ResponseEntity<HttpStatus> deleteGitLabConnection(
            @PathVariable String workspaceSlug,
            @PathVariable Long connectionId
    ) {
        Long workspaceId = workspaceService.getWorkspaceBySlug(workspaceSlug).getId();
        vcsConnectionService.deleteGitLabConnection(workspaceId, connectionId);
        return new ResponseEntity<>(HttpStatus.OK);
    }

    @GetMapping("/connections/{connectionId}")
    @PreAuthorize("@workspaceSecurity.hasOwnerOrAdminRights(#workspaceSlug, authentication)")
    public ResponseEntity<GitLabDTO> getConnectionInfo(
            @PathVariable String workspaceSlug,
            @PathVariable Long connectionId
    ) {
        Long workspaceId = workspaceService.getWorkspaceBySlug(workspaceSlug).getId();
        VcsConnection connection = vcsConnectionRepository.findByWorkspace_IdAndId(workspaceId, connectionId)
                .orElseThrow(() -> new IllegalArgumentException("VCS connection not found"));

        if (connection.getProviderType() != EVcsProvider.GITLAB) {
            throw new IllegalArgumentException("Not a GitLab connection");
        }
        
        return new ResponseEntity<>(GitLabDTO.fromVcsConnection(connection), HttpStatus.OK);
    }

    @GetMapping("/{connectionId}/repositories")
    @PreAuthorize("@workspaceSecurity.hasOwnerOrAdminRights(#workspaceSlug, authentication)")
    public ResponseEntity<RepositorySearchResult> getGitLabConnectionRepositories(
            @PathVariable String workspaceSlug,
            @PathVariable Long connectionId,
            @RequestParam(value = "q", required = false) String query,
            @RequestParam(defaultValue = "1") int page
    ) throws IOException {
        Long workspaceId = workspaceService.getWorkspaceBySlug(workspaceSlug).getId();
        RepositorySearchResult repos = vcsConnectionService.searchGitLabRepositories(
                workspaceId, connectionId, query, page);

        return new ResponseEntity<>(repos, HttpStatus.OK);
    }

    /**
     * Create a GitLab connection using a Project Access Token (repository-scoped token).
     * Project Access Tokens are limited to a single project/repository.
     */
    @PostMapping("/create-repository-token")
    @PreAuthorize("@workspaceSecurity.hasOwnerOrAdminRights(#workspaceSlug, authentication)")
    public ResponseEntity<GitLabDTO> createGitLabRepositoryTokenConnection(
            @PathVariable String workspaceSlug,
            @RequestBody GitLabRepositoryTokenRequest request
    ) {
        Long workspaceId = workspaceService.getWorkspaceBySlug(workspaceSlug).getId();

        VcsConnection createdConnection = vcsConnectionService.createGitLabRepositoryTokenConnection(
                workspaceId,
                request
        );

        return new ResponseEntity<>(GitLabDTO.fromVcsConnection(createdConnection), HttpStatus.CREATED);
    }
}
