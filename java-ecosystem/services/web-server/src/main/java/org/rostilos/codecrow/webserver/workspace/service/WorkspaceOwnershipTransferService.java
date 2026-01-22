package org.rostilos.codecrow.webserver.workspace.service;

import org.rostilos.codecrow.core.model.user.User;
import org.rostilos.codecrow.core.model.workspace.*;
import org.rostilos.codecrow.core.persistence.repository.user.UserRepository;
import org.rostilos.codecrow.core.persistence.repository.workspace.WorkspaceMemberRepository;
import org.rostilos.codecrow.core.persistence.repository.workspace.WorkspaceOwnershipTransferRepository;
import org.rostilos.codecrow.core.persistence.repository.workspace.WorkspaceRepository;
import org.rostilos.codecrow.webserver.auth.service.TwoFactorAuthService;
import org.rostilos.codecrow.webserver.exception.TwoFactorRequiredException;
import org.rostilos.codecrow.webserver.workspace.dto.response.OwnershipTransferDTO;
import org.slf4j.Logger;
import org.slf4j.LoggerFactory;
import org.springframework.scheduling.annotation.Scheduled;
import org.springframework.security.authentication.BadCredentialsException;
import org.springframework.stereotype.Service;
import org.springframework.transaction.annotation.Transactional;

import java.time.Instant;
import java.util.List;
import java.util.NoSuchElementException;
import java.util.Optional;
import java.util.UUID;

@Service
public class WorkspaceOwnershipTransferService {

    private static final Logger logger = LoggerFactory.getLogger(WorkspaceOwnershipTransferService.class);

    private final WorkspaceOwnershipTransferRepository transferRepository;
    private final WorkspaceMemberRepository workspaceMemberRepository;
    private final WorkspaceRepository workspaceRepository;
    private final UserRepository userRepository;
    private final TwoFactorAuthService twoFactorAuthService;

    public WorkspaceOwnershipTransferService(
            WorkspaceOwnershipTransferRepository transferRepository,
            WorkspaceMemberRepository workspaceMemberRepository,
            WorkspaceRepository workspaceRepository,
            UserRepository userRepository,
            TwoFactorAuthService twoFactorAuthService) {
        this.transferRepository = transferRepository;
        this.workspaceMemberRepository = workspaceMemberRepository;
        this.workspaceRepository = workspaceRepository;
        this.userRepository = userRepository;
        this.twoFactorAuthService = twoFactorAuthService;
    }

    /**
     * Initiate ownership transfer. Requires 2FA verification from current owner.
     */
    @Transactional
    public WorkspaceOwnershipTransfer initiateTransfer(Long ownerId, Long workspaceId, Long targetUserId, String twoFactorCode) {
        Workspace workspace = workspaceRepository.findById(workspaceId)
                .orElseThrow(() -> new NoSuchElementException("Workspace not found"));

        WorkspaceMember ownerMember = workspaceMemberRepository.findByWorkspaceIdAndUserId(workspaceId, ownerId)
                .orElseThrow(() -> new SecurityException("User is not a member of this workspace"));

        if (ownerMember.getRole() != EWorkspaceRole.OWNER) {
            throw new SecurityException("Only the workspace owner can transfer ownership");
        }

        WorkspaceMember targetMember = workspaceMemberRepository.findByWorkspaceIdAndUserId(workspaceId, targetUserId)
                .orElseThrow(() -> new IllegalArgumentException("Target user is not a member of this workspace"));

        if (targetMember.getStatus() != EMembershipStatus.ACTIVE) {
            throw new IllegalArgumentException("Target user must have active membership status");
        }

        if (targetUserId.equals(ownerId)) {
            throw new IllegalArgumentException("Cannot transfer ownership to yourself");
        }

        Optional<WorkspaceOwnershipTransfer> existingTransfer = transferRepository.findPendingByWorkspaceId(workspaceId);
        if (existingTransfer.isPresent()) {
            throw new IllegalStateException("There is already a pending ownership transfer for this workspace. " +
                    "Cancel it first before initiating a new one.");
        }

        boolean has2FA = verify2FACode(ownerId, twoFactorCode);
        if (!has2FA) {
            throw new TwoFactorRequiredException("Two-factor authentication is required to transfer ownership");
        }

        WorkspaceOwnershipTransfer transfer = new WorkspaceOwnershipTransfer(workspace, ownerId, targetUserId);
        transfer.setTwoFactorVerified(true);

        WorkspaceOwnershipTransfer savedTransfer = transferRepository.save(transfer);

        logger.info("Ownership transfer initiated for workspace {} from user {} to user {}",
                workspace.getSlug(), ownerId, targetUserId);

        return savedTransfer;
    }

    /**
     * Cancel a pending ownership transfer. Only the current owner can cancel.
     */
    @Transactional
    public void cancelTransfer(UUID transferId, Long ownerId, String reason) {
        WorkspaceOwnershipTransfer transfer = transferRepository.findById(transferId)
                .orElseThrow(() -> new NoSuchElementException("Transfer not found"));

        if (!transfer.getFromUserId().equals(ownerId)) {
            throw new SecurityException("Only the workspace owner who initiated the transfer can cancel it");
        }

        if (!transfer.canBeCancelled()) {
            throw new IllegalStateException("This transfer cannot be cancelled. It may have already been completed, cancelled, or expired.");
        }

        transfer.setStatus(WorkspaceOwnershipTransfer.TransferStatus.CANCELLED);
        transfer.setCancelledAt(Instant.now());
        transfer.setCancellationReason(reason);
        transferRepository.save(transfer);

        logger.info("Ownership transfer {} cancelled by owner {}. Reason: {}",
                transferId, ownerId, reason != null ? reason : "No reason provided");
    }

    /**
     * Complete an ownership transfer. Can be called by either the current owner or the new owner.
     */
    @Transactional
    public void completeTransfer(UUID transferId, Long userId) {
        WorkspaceOwnershipTransfer transfer = transferRepository.findById(transferId)
                .orElseThrow(() -> new NoSuchElementException("Transfer not found"));

        boolean isInitiator = transfer.getFromUserId().equals(userId);
        boolean isRecipient = transfer.getToUserId().equals(userId);

        if (!isInitiator && !isRecipient) {
            throw new SecurityException("Only the current owner or the new owner can complete this transfer");
        }

        if (!transfer.canBeCompleted()) {
            throw new IllegalStateException("This transfer cannot be completed. It may have already been completed, cancelled, or expired.");
        }

        executeOwnershipSwap(transfer);

        transfer.setStatus(WorkspaceOwnershipTransfer.TransferStatus.COMPLETED);
        transfer.setCompletedAt(Instant.now());
        transferRepository.save(transfer);

        logger.info("Ownership transfer {} completed. Workspace {} now owned by user {}",
                transferId, transfer.getWorkspace().getSlug(), transfer.getToUserId());
    }

    @Transactional(readOnly = true)
    public Optional<WorkspaceOwnershipTransfer> getPendingTransfer(Long workspaceId) {
        return transferRepository.findPendingByWorkspaceId(workspaceId);
    }

    @Transactional(readOnly = true)
    public Optional<WorkspaceOwnershipTransfer> getPendingTransfer(String workspaceSlug) {
        Workspace workspace = workspaceRepository.findBySlug(workspaceSlug)
                .orElseThrow(() -> new NoSuchElementException("Workspace not found"));
        return getPendingTransfer(workspace.getId());
    }

    @Transactional(readOnly = true)
    public List<WorkspaceOwnershipTransfer> getTransferHistory(Long workspaceId) {
        return transferRepository.findByWorkspaceIdOrderByInitiatedAtDesc(workspaceId);
    }

    @Scheduled(fixedRate = 3600000)
    @Transactional
    public void autoCompleteExpiredTransfers() {
        List<WorkspaceOwnershipTransfer> pendingTransfers = transferRepository.findExpiredPendingTransfers(Instant.now());
        
        for (WorkspaceOwnershipTransfer transfer : pendingTransfers) {
            try {
                executeOwnershipSwap(transfer);
                transfer.setStatus(WorkspaceOwnershipTransfer.TransferStatus.COMPLETED);
                transfer.setCompletedAt(Instant.now());
                transferRepository.save(transfer);
                
                logger.info("Auto-completed ownership transfer {} after 24h waiting period. Workspace {} now owned by user {}",
                        transfer.getId(), transfer.getWorkspace().getSlug(), transfer.getToUserId());
            } catch (Exception e) {
                logger.error("Failed to auto-complete transfer {}: {}", transfer.getId(), e.getMessage());
                transfer.setStatus(WorkspaceOwnershipTransfer.TransferStatus.EXPIRED);
                transferRepository.save(transfer);
            }
        }
    }

    private void executeOwnershipSwap(WorkspaceOwnershipTransfer transfer) {
        Long workspaceId = transfer.getWorkspace().getId();
        Long fromUserId = transfer.getFromUserId();
        Long toUserId = transfer.getToUserId();

        WorkspaceMember fromMember = workspaceMemberRepository.findByWorkspaceIdAndUserId(workspaceId, fromUserId)
                .orElseThrow(() -> new IllegalStateException("Original owner is no longer a member of the workspace"));

        WorkspaceMember toMember = workspaceMemberRepository.findByWorkspaceIdAndUserId(workspaceId, toUserId)
                .orElseThrow(() -> new IllegalStateException("New owner is no longer a member of the workspace"));

        fromMember.setRole(EWorkspaceRole.ADMIN);
        toMember.setRole(EWorkspaceRole.OWNER);

        workspaceMemberRepository.save(fromMember);
        workspaceMemberRepository.save(toMember);
    }

    private boolean verify2FACode(Long userId, String code) {
        if (code == null || code.isBlank()) {
            return false;
        }
        try {
            return twoFactorAuthService.verifyLoginCode(userId, code);
        } catch (NoSuchElementException e) {
            throw new TwoFactorRequiredException("Two-factor authentication must be enabled to transfer workspace ownership");
        } catch (BadCredentialsException e) {
            return false;
        }
    }

    /**
     * Convert a transfer entity to DTO with user information.
     */
    public OwnershipTransferDTO toDTO(WorkspaceOwnershipTransfer transfer) {
        User fromUser = userRepository.findById(transfer.getFromUserId()).orElse(null);
        User toUser = userRepository.findById(transfer.getToUserId()).orElse(null);
        return OwnershipTransferDTO.fromEntity(transfer, fromUser, toUser);
    }

    /**
     * Get pending transfer as DTO for a workspace, if any.
     */
    @Transactional(readOnly = true)
    public Optional<OwnershipTransferDTO> getPendingTransferDTO(String workspaceSlug) {
        return getPendingTransfer(workspaceSlug).map(this::toDTO);
    }

    /**
     * Get transfer history as DTOs for a workspace.
     */
    @Transactional(readOnly = true)
    public List<OwnershipTransferDTO> getTransferHistoryDTO(Long workspaceId) {
        return getTransferHistory(workspaceId).stream()
                .map(this::toDTO)
                .toList();
    }
}
