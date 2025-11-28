package org.rostilos.codecrow.core.model.workspace;

import jakarta.persistence.*;

import org.rostilos.codecrow.core.model.user.User;
import org.springframework.data.annotation.CreatedDate;
import org.springframework.data.jpa.domain.support.AuditingEntityListener;

import java.time.Instant;

@Entity
@Table(name = "workspace_member", uniqueConstraints = {
        @UniqueConstraint(name = "uq_workspace_member_user", columnNames = {"workspace_id", "user_id"})
})
@EntityListeners(AuditingEntityListener.class)
public class WorkspaceMember {

    @Id
    @GeneratedValue
    @Column(nullable = false, updatable = false)
    private Long id;

    @ManyToOne(fetch = FetchType.LAZY, optional = false)
    @JoinColumn(name = "workspace_id", nullable = false)
    private Workspace workspace;

    @ManyToOne(fetch = FetchType.LAZY, optional = false)
    @JoinColumn(name = "user_id", nullable = false)
    private User user;

    @Enumerated(EnumType.STRING)
    @Column(name = "role", nullable = false, length = 32)
    private EWorkspaceRole role = EWorkspaceRole.MEMBER;

    @Enumerated(EnumType.STRING)
    @Column(name = "status", nullable = false, length = 20)
    private EMembershipStatus status = EMembershipStatus.ACTIVE;

    @CreatedDate
    @Column(name = "joined_at", nullable = false, updatable = false)
    private Instant joinedAt;

    public WorkspaceMember() {
    }

    public WorkspaceMember(Workspace workspace, User user, EWorkspaceRole role, EMembershipStatus status) {
        this.workspace = workspace;
        this.user = user;
        this.role = role;
        this.status = status;
    }

    public Long getId() {
        return id;
    }

    public Workspace getWorkspace() {
        return workspace;
    }

    public void setWorkspace(Workspace workspace) {
        this.workspace = workspace;
    }

    public User getUser() {
        return user;
    }

    public void setUser(User user) {
        this.user = user;
    }

    public EWorkspaceRole getRole() {
        return role;
    }

    public void setRole(EWorkspaceRole role) {
        this.role = role;
    }

    public EMembershipStatus getStatus() {
        return status;
    }

    public void setStatus(EMembershipStatus status) {
        this.status = status;
    }

    public Instant getJoinedAt() {
        return joinedAt;
    }
}
