package org.rostilos.codecrow.core.model.project;

import com.fasterxml.jackson.annotation.JsonBackReference;
import jakarta.persistence.*;
import org.rostilos.codecrow.core.model.vcs.EVcsProvider;
import org.rostilos.codecrow.core.model.vcs.VcsConnection;

import java.util.UUID;

@Entity
@Table(name = "project_vcs_connection", uniqueConstraints = {
        @UniqueConstraint(name = "uq_repo_unique", columnNames = {"repository_id"})
})
public class ProjectVcsConnectionBinding {

    @Id
    @GeneratedValue
    @Column(nullable = false, updatable = false)
    private Long id;

    @OneToOne(fetch = FetchType.LAZY)
    @JoinColumn(name = "project_id", nullable = false)
    @JsonBackReference
    private Project project;

    @Enumerated(EnumType.STRING)
    @Column(name = "vcs_provider", nullable = true, length = 32)
    private EVcsProvider vcsProvider;

    @ManyToOne(fetch = FetchType.LAZY)
    @JoinColumn(name = "connection_id", nullable = false)
    private VcsConnection vcsConnection;

    @Column(name = "repository_id", nullable = false, updatable = false, length = 64)
    private UUID repositoryUUID;

    @Column(name = "workspace", length = 128)
    private String workspace;

    @Column(name = "repo_slug", length = 256)
    private String repoSlug;

    @Column(name = "display_name", length = 256)
    private String displayName;

    public Long getId() {
        return id;
    }

    public EVcsProvider getVcsProvider() {
        return vcsProvider;
    }

    public void setVcsProvider(EVcsProvider vcsProvider) {
        this.vcsProvider = vcsProvider;
    }

    public UUID getRepositoryUUID() {
        return repositoryUUID;
    }

    public void setRepositoryUUID(UUID repositoryUUID) {
        this.repositoryUUID = repositoryUUID;
    }

    public String getWorkspace() {
        return workspace;
    }

    public void setWorkspace(String workspace) {
        this.workspace = workspace;
    }

    public String getRepoSlug() {
        return repoSlug;
    }

    public void setRepoSlug(String repoSlug) {
        this.repoSlug = repoSlug;
    }

    public String getDisplayName() {
        return displayName;
    }

    public void setDisplayName(String displayName) {
        this.displayName = displayName;
    }

    public Project getProject() {
        return project;
    }

    public void setProject(Project project) {
        this.project = project;
    }

    public VcsConnection getVcsConnection() {
        return vcsConnection;
    }

    public void setVcsConnection(VcsConnection vcsConnection) {
        this.vcsConnection = vcsConnection;
    }
}
