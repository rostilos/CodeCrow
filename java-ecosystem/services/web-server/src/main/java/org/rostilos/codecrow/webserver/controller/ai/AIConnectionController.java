package org.rostilos.codecrow.webserver.controller.ai;

import static org.springframework.http.MediaType.APPLICATION_JSON_VALUE;

import jakarta.validation.Valid;
import org.rostilos.codecrow.core.model.ai.AIConnection;
import org.rostilos.codecrow.core.model.workspace.Workspace;
import org.rostilos.codecrow.webserver.dto.request.ai.CreateAIConnectionRequest;
import org.rostilos.codecrow.webserver.dto.request.ai.UpdateAiConnectionRequest;
import org.rostilos.codecrow.core.dto.ai.AIConnectionDTO;
import org.rostilos.codecrow.webserver.service.ai.AIConnectionService;
import org.rostilos.codecrow.webserver.service.workspace.WorkspaceService;
import org.springframework.http.HttpStatus;
import org.springframework.http.ResponseEntity;
import org.springframework.security.access.prepost.PreAuthorize;
import org.springframework.web.bind.annotation.*;

import java.security.GeneralSecurityException;
import java.util.List;

@CrossOrigin(origins = "*", maxAge = 3600)
@RestController
@RequestMapping(path = "/api/{workspaceSlug}/ai", produces = APPLICATION_JSON_VALUE)
public class AIConnectionController {

    private final AIConnectionService aiConnectionService;
    private final WorkspaceService workspaceService;

    public AIConnectionController(
            AIConnectionService aiConnectionService,
            WorkspaceService workspaceService
    ) {
        this.aiConnectionService = aiConnectionService;
        this.workspaceService = workspaceService;
    }

    @GetMapping("/list")
    @PreAuthorize("@workspaceSecurity.isWorkspaceMember(#workspaceSlug, authentication)")
    public ResponseEntity<List<AIConnectionDTO>> listWorkspaceConnections(@PathVariable String workspaceSlug) {
        Workspace workspace = workspaceService.getWorkspaceBySlug(workspaceSlug);
        List<AIConnectionDTO> workspaceConnections = aiConnectionService.listWorkspaceConnections(workspace.getId())
                .stream()
                .map(AIConnectionDTO::fromAiConnection)
                .toList();

        return new ResponseEntity<>(workspaceConnections, HttpStatus.OK);
    }

    @PostMapping("/create")
    @PreAuthorize("@workspaceSecurity.hasOwnerOrAdminRights(#workspaceSlug, authentication)")
    public ResponseEntity<AIConnectionDTO> createConnection(
            @PathVariable String workspaceSlug,
            @Valid @RequestBody CreateAIConnectionRequest request
    ) throws GeneralSecurityException {
        Workspace workspace = workspaceService.getWorkspaceBySlug(workspaceSlug);
        AIConnection created = aiConnectionService.createAiConnection(workspace.getId(), request);
        return new ResponseEntity<>(AIConnectionDTO.fromAiConnection(created), HttpStatus.CREATED);
    }

    @PatchMapping("/{connectionId}")
    @PreAuthorize("@workspaceSecurity.hasOwnerOrAdminRights(#workspaceSlug, authentication)")
    public ResponseEntity<AIConnectionDTO> updateConnection(
            @PathVariable String workspaceSlug,
            @PathVariable Long connectionId,
            @Valid @RequestBody UpdateAiConnectionRequest request
    ) throws GeneralSecurityException {
        Workspace workspace = workspaceService.getWorkspaceBySlug(workspaceSlug);
        AIConnection updated = aiConnectionService.updateAiConnection(workspace.getId(), connectionId, request);
        return new ResponseEntity<>(AIConnectionDTO.fromAiConnection(updated), HttpStatus.OK);
    }


    @DeleteMapping("/connections/{connectionId}")
    @PreAuthorize("@workspaceSecurity.hasOwnerOrAdminRights(#workspaceSlug, authentication)")
    public ResponseEntity<Void> deleteConnection(
            @PathVariable String workspaceSlug,
            @PathVariable Long connectionId
    ) {
        Workspace workspace = workspaceService.getWorkspaceBySlug(workspaceSlug);
        aiConnectionService.deleteAiConnection(workspace.getId(), connectionId);
        return new ResponseEntity<>(HttpStatus.NO_CONTENT);
    }
}
