package org.rostilos.codecrow.webserver.qualitygate.controller;

import static org.springframework.http.MediaType.APPLICATION_JSON_VALUE;

import jakarta.validation.Valid;
import org.rostilos.codecrow.core.dto.qualitygate.QualityGateDTO;
import org.rostilos.codecrow.core.model.qualitygate.QualityGate;
import org.rostilos.codecrow.core.model.workspace.Workspace;
import org.rostilos.codecrow.security.annotations.HasOwnerOrAdminRights;
import org.rostilos.codecrow.security.annotations.IsWorkspaceMember;
import org.rostilos.codecrow.webserver.qualitygate.dto.request.CreateQualityGateRequest;
import org.rostilos.codecrow.webserver.qualitygate.dto.request.UpdateQualityGateRequest;
import org.rostilos.codecrow.webserver.qualitygate.service.QualityGateService;
import org.rostilos.codecrow.webserver.workspace.service.WorkspaceService;
import org.springframework.http.HttpStatus;
import org.springframework.http.ResponseEntity;
import org.springframework.web.bind.annotation.*;

import java.util.List;

@CrossOrigin(origins = "*", maxAge = 3600)
@RestController
@RequestMapping(path = "/api/{workspaceSlug}/quality-gates", produces = APPLICATION_JSON_VALUE)
public class QualityGateController {

    private final QualityGateService qualityGateService;
    private final WorkspaceService workspaceService;

    public QualityGateController(
            QualityGateService qualityGateService,
            WorkspaceService workspaceService
    ) {
        this.qualityGateService = qualityGateService;
        this.workspaceService = workspaceService;
    }

    @GetMapping
    @IsWorkspaceMember
    public ResponseEntity<List<QualityGateDTO>> listQualityGates(@PathVariable String workspaceSlug) {
        Workspace workspace = workspaceService.getWorkspaceBySlug(workspaceSlug);
        // Ensure a default quality gate exists, creating one if needed
        try {
            qualityGateService.ensureDefaultQualityGate(workspace.getId());
        } catch (RuntimeException e) {
            // Race condition handler - default gate exists now, just continue listing
        }
        
        List<QualityGateDTO> qualityGates = qualityGateService.listWorkspaceQualityGates(workspace.getId())
                .stream()
                .map(QualityGateDTO::fromEntity)
                .toList();

        return new ResponseEntity<>(qualityGates, HttpStatus.OK);
    }

    @GetMapping("/{qualityGateId}")
    @IsWorkspaceMember
    public ResponseEntity<QualityGateDTO> getQualityGate(
            @PathVariable String workspaceSlug,
            @PathVariable Long qualityGateId
    ) {
        Workspace workspace = workspaceService.getWorkspaceBySlug(workspaceSlug);
        QualityGate qualityGate = qualityGateService.getQualityGate(workspace.getId(), qualityGateId);
        return new ResponseEntity<>(QualityGateDTO.fromEntity(qualityGate), HttpStatus.OK);
    }

    @GetMapping("/default")
    @IsWorkspaceMember
    public ResponseEntity<QualityGateDTO> getDefaultQualityGate(@PathVariable String workspaceSlug) {
        Workspace workspace = workspaceService.getWorkspaceBySlug(workspaceSlug);
        // Ensure a default quality gate exists, creating one if needed
        try {
            QualityGate qualityGate = qualityGateService.ensureDefaultQualityGate(workspace.getId());
            return new ResponseEntity<>(QualityGateDTO.fromEntity(qualityGate), HttpStatus.OK);
        } catch (RuntimeException e) {
            // Race condition - fetch the one that was created
            QualityGate qualityGate = qualityGateService.getDefaultQualityGate(workspace.getId());
            if (qualityGate != null) {
                return new ResponseEntity<>(QualityGateDTO.fromEntity(qualityGate), HttpStatus.OK);
            }
            throw e;
        }
    }

    @PostMapping
    @HasOwnerOrAdminRights
    public ResponseEntity<QualityGateDTO> createQualityGate(
            @PathVariable String workspaceSlug,
            @Valid @RequestBody CreateQualityGateRequest request
    ) {
        Workspace workspace = workspaceService.getWorkspaceBySlug(workspaceSlug);
        QualityGate created = qualityGateService.createQualityGate(workspace.getId(), request);
        return new ResponseEntity<>(QualityGateDTO.fromEntity(created), HttpStatus.CREATED);
    }

    @PutMapping("/{qualityGateId}")
    @HasOwnerOrAdminRights
    public ResponseEntity<QualityGateDTO> updateQualityGate(
            @PathVariable String workspaceSlug,
            @PathVariable Long qualityGateId,
            @Valid @RequestBody UpdateQualityGateRequest request
    ) {
        Workspace workspace = workspaceService.getWorkspaceBySlug(workspaceSlug);
        QualityGate updated = qualityGateService.updateQualityGate(workspace.getId(), qualityGateId, request);
        return new ResponseEntity<>(QualityGateDTO.fromEntity(updated), HttpStatus.OK);
    }

    @PostMapping("/{qualityGateId}/set-default")
    @HasOwnerOrAdminRights
    public ResponseEntity<QualityGateDTO> setDefault(
            @PathVariable String workspaceSlug,
            @PathVariable Long qualityGateId
    ) {
        Workspace workspace = workspaceService.getWorkspaceBySlug(workspaceSlug);
        QualityGate updated = qualityGateService.setDefault(workspace.getId(), qualityGateId);
        return new ResponseEntity<>(QualityGateDTO.fromEntity(updated), HttpStatus.OK);
    }

    @DeleteMapping("/{qualityGateId}")
    @HasOwnerOrAdminRights
    public ResponseEntity<Void> deleteQualityGate(
            @PathVariable String workspaceSlug,
            @PathVariable Long qualityGateId
    ) {
        Workspace workspace = workspaceService.getWorkspaceBySlug(workspaceSlug);
        qualityGateService.deleteQualityGate(workspace.getId(), qualityGateId);
        return new ResponseEntity<>(HttpStatus.NO_CONTENT);
    }
}
