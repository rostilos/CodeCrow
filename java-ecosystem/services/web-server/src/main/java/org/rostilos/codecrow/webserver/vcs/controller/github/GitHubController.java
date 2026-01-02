package org.rostilos.codecrow.webserver.vcs.controller.github;

import java.io.IOException;
import java.util.List;

import org.rostilos.codecrow.core.dto.github.GitHubDTO;
import org.rostilos.codecrow.core.model.vcs.EVcsProvider;
import org.rostilos.codecrow.core.model.vcs.VcsConnection;
import org.rostilos.codecrow.core.model.vcs.config.github.GitHubConfig;
import org.rostilos.codecrow.core.persistence.repository.vcs.VcsConnectionRepository;
import org.rostilos.codecrow.vcsclient.github.dto.response.RepositorySearchResult;
import org.rostilos.codecrow.webserver.vcs.dto.request.github.GitHubCreateRequest;
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
@RequestMapping("/api/{workspaceSlug}/vcs/github")
public class GitHubController {
    
    private final VcsConnectionRepository vcsConnectionRepository;
    private final VcsConnectionWebService vcsConnectionService;
    private final WorkspaceService workspaceService;

    public GitHubController(
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
    public ResponseEntity<List<GitHubDTO>> getGitHubConnections(@PathVariable String workspaceSlug) {
        Long workspaceId = workspaceService.getWorkspaceBySlug(workspaceSlug).getId();
        List<VcsConnection> connections = vcsConnectionService.getWorkspaceGitHubConnections(workspaceId);
        List<GitHubDTO> connectionDTOs = connections.stream()
                .map(GitHubDTO::fromVcsConnection)
                .toList();

        return new ResponseEntity<>(connectionDTOs, HttpStatus.OK);
    }

    @PostMapping("/create")
    @PreAuthorize("@workspaceSecurity.hasOwnerOrAdminRights(#workspaceSlug, authentication)")
    public ResponseEntity<GitHubDTO> createGitHubConnection(
            @PathVariable String workspaceSlug,
            @RequestBody GitHubCreateRequest request
    ) {
        Long workspaceId = workspaceService.getWorkspaceBySlug(workspaceSlug).getId();

        GitHubConfig config = new GitHubConfig(
                request.getAccessToken(),
                request.getOrganizationId(),
                null
        );

        VcsConnection createdConnection = vcsConnectionService.createGitHubConnection(
                workspaceId,
                config,
                request.getConnectionName()
        );

        return new ResponseEntity<>(GitHubDTO.fromVcsConnection(createdConnection), HttpStatus.CREATED);
    }

    @PatchMapping("/connections/{connectionId}")
    @PreAuthorize("@workspaceSecurity.hasOwnerOrAdminRights(#workspaceSlug, authentication)")
    public ResponseEntity<GitHubDTO> updateGitHubConnection(
            @PathVariable String workspaceSlug,
            @PathVariable Long connectionId,
            @RequestBody GitHubCreateRequest request
    ) {
        Long workspaceId = workspaceService.getWorkspaceBySlug(workspaceSlug).getId();
        VcsConnection updatedConnection = vcsConnectionService.updateGitHubConnection(
                workspaceId,
                connectionId,
                request
        );
        return new ResponseEntity<>(GitHubDTO.fromVcsConnection(updatedConnection), HttpStatus.OK);
    }

    @DeleteMapping("/connections/{connectionId}")
    @PreAuthorize("@workspaceSecurity.hasOwnerOrAdminRights(#workspaceSlug, authentication)")
    public ResponseEntity<HttpStatus> deleteGitHubConnection(
            @PathVariable String workspaceSlug,
            @PathVariable Long connectionId
    ) {
        Long workspaceId = workspaceService.getWorkspaceBySlug(workspaceSlug).getId();
        vcsConnectionService.deleteGitHubConnection(workspaceId, connectionId);
        return new ResponseEntity<>(HttpStatus.OK);
    }

    @GetMapping("/connections/{connectionId}")
    @PreAuthorize("@workspaceSecurity.hasOwnerOrAdminRights(#workspaceSlug, authentication)")
    public ResponseEntity<GitHubDTO> getConnectionInfo(
            @PathVariable String workspaceSlug,
            @PathVariable Long connectionId
    ) {
        Long workspaceId = workspaceService.getWorkspaceBySlug(workspaceSlug).getId();
        VcsConnection connection = vcsConnectionRepository.findByWorkspace_IdAndId(workspaceId, connectionId)
                .orElseThrow(() -> new IllegalArgumentException("VCS connection not found"));

        if (connection.getProviderType() != EVcsProvider.GITHUB) {
            throw new IllegalArgumentException("Not a GitHub connection");
        }
        
        return new ResponseEntity<>(GitHubDTO.fromVcsConnection(connection), HttpStatus.OK);
    }

    @GetMapping("/{connectionId}/repositories")
    @PreAuthorize("@workspaceSecurity.hasOwnerOrAdminRights(#workspaceSlug, authentication)")
    public ResponseEntity<RepositorySearchResult> getGitHubConnectionRepositories(
            @PathVariable String workspaceSlug,
            @PathVariable Long connectionId,
            @RequestParam(value = "q", required = false) String query,
            @RequestParam(defaultValue = "1") int page
    ) throws IOException {
        Long workspaceId = workspaceService.getWorkspaceBySlug(workspaceSlug).getId();
        RepositorySearchResult repos = vcsConnectionService.searchGitHubRepositories(
                workspaceId, connectionId, query, page);

        return new ResponseEntity<>(repos, HttpStatus.OK);
    }
}
