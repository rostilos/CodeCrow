package org.rostilos.codecrow.core.model.workspace;

import jakarta.persistence.*;
import java.time.Instant;
import java.util.UUID;

/**
 * Entity to track pending workspace ownership transfers.
 * Implements a 24-hour pending state before transfer is finalized.
 * Current owner can cancel within this period.
 */
@Entity
@Table(name = "workspace_ownership_transfer")
public class WorkspaceOwnershipTransfer {

    public enum TransferStatus {
        PENDING,      // Transfer initiated, waiting for 24h or confirmation
        COMPLETED,    // Transfer finalized
        CANCELLED,    // Cancelled by current owner
        EXPIRED       // 24h passed without confirmation (auto-cancelled)
    }

    @Id
    @GeneratedValue(strategy = GenerationType.UUID)
    private UUID id;

    @ManyToOne(fetch = FetchType.LAZY)
    @JoinColumn(name = "workspace_id", nullable = false)
    private Workspace workspace;

    @Column(name = "from_user_id", nullable = false)
    private Long fromUserId;

    @Column(name = "to_user_id", nullable = false)
    private Long toUserId;

    @Enumerated(EnumType.STRING)
    @Column(nullable = false)
    private TransferStatus status = TransferStatus.PENDING;

    @Column(name = "initiated_at", nullable = false)
    private Instant initiatedAt;

    @Column(name = "expires_at", nullable = false)
    private Instant expiresAt;

    @Column(name = "completed_at")
    private Instant completedAt;

    @Column(name = "cancelled_at")
    private Instant cancelledAt;

    @Column(name = "cancellation_reason")
    private String cancellationReason;

    @Column(name = "two_factor_verified", nullable = false)
    private boolean twoFactorVerified = false;

    public WorkspaceOwnershipTransfer() {
    }

    public WorkspaceOwnershipTransfer(Workspace workspace, Long fromUserId, Long toUserId) {
        this.workspace = workspace;
        this.fromUserId = fromUserId;
        this.toUserId = toUserId;
        this.initiatedAt = Instant.now();
        this.expiresAt = this.initiatedAt.plusSeconds(24 * 60 * 60); // 24 hours
        this.status = TransferStatus.PENDING;
    }

    public boolean isExpired() {
        return Instant.now().isAfter(expiresAt) && status == TransferStatus.PENDING;
    }

    public boolean canBeCancelled() {
        return status == TransferStatus.PENDING && !isExpired();
    }

    public boolean canBeCompleted() {
        return status == TransferStatus.PENDING && twoFactorVerified;
    }


    public UUID getId() {
        return id;
    }

    public void setId(UUID id) {
        this.id = id;
    }

    public Workspace getWorkspace() {
        return workspace;
    }

    public void setWorkspace(Workspace workspace) {
        this.workspace = workspace;
    }

    public Long getFromUserId() {
        return fromUserId;
    }

    public void setFromUserId(Long fromUserId) {
        this.fromUserId = fromUserId;
    }

    public Long getToUserId() {
        return toUserId;
    }

    public void setToUserId(Long toUserId) {
        this.toUserId = toUserId;
    }

    public TransferStatus getStatus() {
        return status;
    }

    public void setStatus(TransferStatus status) {
        this.status = status;
    }

    public Instant getInitiatedAt() {
        return initiatedAt;
    }

    public void setInitiatedAt(Instant initiatedAt) {
        this.initiatedAt = initiatedAt;
    }

    public Instant getExpiresAt() {
        return expiresAt;
    }

    public void setExpiresAt(Instant expiresAt) {
        this.expiresAt = expiresAt;
    }

    public Instant getCompletedAt() {
        return completedAt;
    }

    public void setCompletedAt(Instant completedAt) {
        this.completedAt = completedAt;
    }

    public Instant getCancelledAt() {
        return cancelledAt;
    }

    public void setCancelledAt(Instant cancelledAt) {
        this.cancelledAt = cancelledAt;
    }

    public String getCancellationReason() {
        return cancellationReason;
    }

    public void setCancellationReason(String cancellationReason) {
        this.cancellationReason = cancellationReason;
    }

    public boolean isTwoFactorVerified() {
        return twoFactorVerified;
    }

    public void setTwoFactorVerified(boolean twoFactorVerified) {
        this.twoFactorVerified = twoFactorVerified;
    }
}
