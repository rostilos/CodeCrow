package org.rostilos.codecrow.webserver.workspace.controller;

import jakarta.validation.Valid;
import org.rostilos.codecrow.core.model.workspace.WorkspaceOwnershipTransfer;
import org.rostilos.codecrow.security.annotations.HasOwnerOrAdminRights;
import org.rostilos.codecrow.security.annotations.IsWorkspaceMember;
import org.rostilos.codecrow.security.annotations.IsWorkspaceOwner;
import org.rostilos.codecrow.security.service.UserDetailsImpl;
import org.rostilos.codecrow.webserver.generic.dto.message.MessageResponse;
import org.rostilos.codecrow.webserver.workspace.dto.request.CancelOwnershipTransferRequest;
import org.rostilos.codecrow.webserver.workspace.dto.request.InitiateOwnershipTransferRequest;
import org.rostilos.codecrow.webserver.workspace.dto.response.OwnershipTransferDTO;
import org.rostilos.codecrow.webserver.workspace.service.WorkspaceOwnershipTransferService;
import org.rostilos.codecrow.webserver.workspace.service.WorkspaceService;
import org.springframework.http.HttpStatus;
import org.springframework.http.ResponseEntity;
import org.springframework.security.core.annotation.AuthenticationPrincipal;
import org.springframework.web.bind.annotation.*;

import java.util.List;
import java.util.Optional;
import java.util.UUID;

@CrossOrigin(origins = "*", maxAge = 3600)
@RestController
@RequestMapping("/api/workspace")
public class WorkspaceOwnershipTransferController {

    private final WorkspaceOwnershipTransferService transferService;
    private final WorkspaceService workspaceService;

    public WorkspaceOwnershipTransferController(
            WorkspaceOwnershipTransferService transferService,
            WorkspaceService workspaceService) {
        this.transferService = transferService;
        this.workspaceService = workspaceService;
    }

    /**
     * Initiate ownership transfer. Requires 2FA from the current owner.
     */
    @PostMapping("/{workspaceSlug}/ownership/transfer")
    @IsWorkspaceOwner
    public ResponseEntity<OwnershipTransferDTO> initiateTransfer(
            @AuthenticationPrincipal UserDetailsImpl userDetails,
            @PathVariable String workspaceSlug,
            @Valid @RequestBody InitiateOwnershipTransferRequest request
    ) {
        Long workspaceId = workspaceService.getWorkspaceBySlug(workspaceSlug).getId();
        WorkspaceOwnershipTransfer transfer = transferService.initiateTransfer(
                userDetails.getId(),
                workspaceId,
                request.targetUserId(),
                request.twoFactorCode()
        );
        return ResponseEntity.status(HttpStatus.CREATED).body(transferService.toDTO(transfer));
    }

    /**
     * Cancel a pending ownership transfer.
     */
    @DeleteMapping("/{workspaceSlug}/ownership/transfer/{transferId}")
    @IsWorkspaceOwner
    public ResponseEntity<MessageResponse> cancelTransfer(
            @AuthenticationPrincipal UserDetailsImpl userDetails,
            @PathVariable String workspaceSlug,
            @PathVariable UUID transferId,
            @RequestBody(required = false) CancelOwnershipTransferRequest request
    ) {
        String reason = request != null ? request.reason() : null;
        transferService.cancelTransfer(transferId, userDetails.getId(), reason);
        return ResponseEntity.ok(new MessageResponse("Ownership transfer cancelled successfully"));
    }

    /**
     * Complete an ownership transfer (by current owner or new owner).
     */
    @PostMapping("/{workspaceSlug}/ownership/transfer/{transferId}/complete")
    @IsWorkspaceMember
    public ResponseEntity<MessageResponse> completeTransfer(
            @AuthenticationPrincipal UserDetailsImpl userDetails,
            @PathVariable String workspaceSlug,
            @PathVariable UUID transferId
    ) {
        transferService.completeTransfer(transferId, userDetails.getId());
        return ResponseEntity.ok(new MessageResponse("Ownership transfer completed successfully"));
    }

    /**
     * Get pending transfer for a workspace, if any.
     */
    @GetMapping("/{workspaceSlug}/ownership/transfer/pending")
    @HasOwnerOrAdminRights
    public ResponseEntity<OwnershipTransferDTO> getPendingTransfer(
            @AuthenticationPrincipal UserDetailsImpl userDetails,
            @PathVariable String workspaceSlug
    ) {
        Optional<OwnershipTransferDTO> transfer = transferService.getPendingTransferDTO(workspaceSlug);
        return transfer
                .map(ResponseEntity::ok)
                .orElse(ResponseEntity.noContent().build());
    }

    /**
     * Get transfer history for a workspace.
     */
    @GetMapping("/{workspaceSlug}/ownership/transfer/history")
    @HasOwnerOrAdminRights
    public ResponseEntity<List<OwnershipTransferDTO>> getTransferHistory(
            @AuthenticationPrincipal UserDetailsImpl userDetails,
            @PathVariable String workspaceSlug
    ) {
        Long workspaceId = workspaceService.getWorkspaceBySlug(workspaceSlug).getId();
        List<OwnershipTransferDTO> history = transferService.getTransferHistoryDTO(workspaceId);
        return ResponseEntity.ok(history);
    }
}
