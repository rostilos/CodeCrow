package org.rostilos.codecrow.core.model.project;

import jakarta.persistence.*;
import org.rostilos.codecrow.core.model.user.User;

@Entity
@Table(name = "project_member", uniqueConstraints = {
        @UniqueConstraint(name = "uq_project_member_user", columnNames = {"project_id", "user_id"})
})
public class ProjectMember {

    @Id
    @GeneratedValue
    @Column(nullable = false, updatable = false)
    private Long id;

    @ManyToOne(fetch = FetchType.LAZY)
    @JoinColumn(name = "project_id", insertable = false, updatable = false)
    private Project project;

    @ManyToOne(fetch = FetchType.LAZY, optional = false)
    @JoinColumn(name = "user_id", nullable = false)
    private User user;

    @Enumerated(EnumType.STRING)
    @Column(name = "role", nullable = false, length = 32)
    private EProjectRole role = EProjectRole.VIEWER;

    public Long getId() {
        return id;
    }

    public Project getProject() {
        return project;
    }

    public void setProject(Project project) {
        this.project = project;
    }

    public User getUser() {
        return user;
    }

    public void setUser(User user) {
        this.user = user;
    }

    public EProjectRole getRole() {
        return role;
    }

    public void setRole(EProjectRole role) {
        this.role = role;
    }
}
