package org.rostilos.codecrow.webserver.controller.vcs.bitbucket.cloud;

import java.io.IOException;
import java.security.GeneralSecurityException;
import java.util.List;

import org.rostilos.codecrow.core.model.vcs.EVcsProvider;
import org.rostilos.codecrow.core.model.vcs.VcsConnection;
import org.rostilos.codecrow.core.persistence.repository.vcs.VcsConnectionRepository;
import org.rostilos.codecrow.vcsclient.bitbucket.cloud.dto.response.RepositorySearchResult;
import org.rostilos.codecrow.webserver.dto.request.vcs.bitbucket.cloud.BitbucketCloudCreateRequest;
import org.rostilos.codecrow.core.dto.bitbucket.BitbucketCloudDTO;
import org.rostilos.codecrow.webserver.dto.message.MessageResponse;
import org.rostilos.codecrow.webserver.service.vcs.VcsConnectionWebService;
import org.rostilos.codecrow.webserver.service.workspace.WorkspaceService;
import org.rostilos.codecrow.webserver.utils.BitbucketCloudConfigHandler;
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
@RequestMapping("/api/{workspaceSlug}/vcs/bitbucket_cloud")
public class BitbucketCloudController {
    private final VcsConnectionRepository vcsConnectionRepository;
    private final VcsConnectionWebService vcsConnectionService;
    private final BitbucketCloudConfigHandler bitbucketCloudConfigBuilder;
    private final WorkspaceService workspaceService;

    public BitbucketCloudController(
            VcsConnectionRepository vcsConnectionRepository,
            VcsConnectionWebService vcsConnectionService,
            BitbucketCloudConfigHandler bitbucketCloudConfigBuilder,
            WorkspaceService workspaceService
    ) {
        this.vcsConnectionRepository = vcsConnectionRepository;
        this.vcsConnectionService = vcsConnectionService;
        this.bitbucketCloudConfigBuilder = bitbucketCloudConfigBuilder;
        this.workspaceService = workspaceService;
    }

    @GetMapping("/list")
    @PreAuthorize("@workspaceSecurity.hasOwnerOrAdminRights(#workspaceSlug, authentication)")
    public ResponseEntity<List<BitbucketCloudDTO>> getUserBitbucketCloudConnections(@PathVariable String workspaceSlug) {
        Long workspaceId = workspaceService.getWorkspaceBySlug(workspaceSlug).getId();
        List<VcsConnection> userBitbucketConnections = vcsConnectionService.getWorkspaceBitbucketCloudConnections(workspaceId);
        List<BitbucketCloudDTO> userBitbucketConnectionsDTOs = userBitbucketConnections.stream()
                .map(BitbucketCloudDTO::fromGitConfiguration)
                .toList();

        return new ResponseEntity<>(userBitbucketConnectionsDTOs, HttpStatus.OK);
    }

    @PostMapping("/create")
    @PreAuthorize("@workspaceSecurity.hasOwnerOrAdminRights(#workspaceSlug, authentication)")
    public ResponseEntity<BitbucketCloudDTO> createUserBitbucketCloudConnection(
            @PathVariable String workspaceSlug,
            @RequestBody BitbucketCloudCreateRequest request
    ) throws GeneralSecurityException {
        Long workspaceId = workspaceService.getWorkspaceBySlug(workspaceSlug).getId();
        VcsConnection createdConnection = vcsConnectionService.createBitbucketCloudConnection(
                workspaceId,
                bitbucketCloudConfigBuilder.buildBitbucketConfigFromRequest(request),
                request.getConnectionName()
        );

        return new ResponseEntity<>(BitbucketCloudDTO.fromGitConfiguration(createdConnection), HttpStatus.CREATED);

    }

    @PatchMapping("/connections/{connectionId}")
    @PreAuthorize("@workspaceSecurity.hasOwnerOrAdminRights(#workspaceSlug, authentication)")
    public ResponseEntity<BitbucketCloudDTO> updateUserBitbucketCloudConnection(
            @PathVariable String workspaceSlug,
            @PathVariable Long connectionId,
            @RequestBody BitbucketCloudCreateRequest request
    ) throws GeneralSecurityException {
        Long workspaceId = workspaceService.getWorkspaceBySlug(workspaceSlug).getId();
        VcsConnection updatedConnection = vcsConnectionService.updateBitbucketCloudConnection(
                workspaceId,
                connectionId,
                request
        );
        return new ResponseEntity<>(BitbucketCloudDTO.fromGitConfiguration(updatedConnection), HttpStatus.OK);
    }

    @DeleteMapping("/connections/{connectionId}")
    @PreAuthorize("@workspaceSecurity.hasOwnerOrAdminRights(#workspaceSlug, authentication)")
    public ResponseEntity<HttpStatus> deleteUserBitbucketCloudConnection(
            @PathVariable String workspaceSlug,
            @PathVariable Long connectionId
    ) {
        Long workspaceId = workspaceService.getWorkspaceBySlug(workspaceSlug).getId();
        vcsConnectionService.deleteBitbucketCloudConnection(workspaceId, connectionId, EVcsProvider.BITBUCKET_CLOUD);
        return new ResponseEntity<>(HttpStatus.OK);
    }

    @GetMapping("/connections/{connectionId}")
    @PreAuthorize("@workspaceSecurity.hasOwnerOrAdminRights(#workspaceSlug, authentication)")
    public ResponseEntity<BitbucketCloudDTO> getConnectionInfo(
            @PathVariable String workspaceSlug,
            @PathVariable Long connectionId
    ) {
        //TODO: service
        Long workspaceId = workspaceService.getWorkspaceBySlug(workspaceSlug).getId();
        VcsConnection connection = vcsConnectionRepository.findByWorkspace_IdAndId(workspaceId, connectionId)
                .orElseThrow(() -> new IllegalArgumentException("VCS connection not found"));

        assert connection != null;
        return new ResponseEntity<>(BitbucketCloudDTO.fromGitConfiguration(connection), HttpStatus.OK);
    }


    @GetMapping("/{connectionId}/repositories")
    @PreAuthorize("@workspaceSecurity.hasOwnerOrAdminRights(#workspaceSlug, authentication)")
    public ResponseEntity<RepositorySearchResult> getBitbucketConnectionRepositories(
            @PathVariable String workspaceSlug,
            @PathVariable Long connectionId,
            @RequestParam(value = "q", required = false) String query,
            @RequestParam(defaultValue = "1") int page
    ) throws IOException {
        Long workspaceId = workspaceService.getWorkspaceBySlug(workspaceSlug).getId();
        RepositorySearchResult userRepos =
                vcsConnectionService.searchBitbucketCloudRepositories(workspaceId, connectionId, query, page);

        return new ResponseEntity<>(userRepos, HttpStatus.OK);
    }
}
