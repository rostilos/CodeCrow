package org.rostilos.codecrow.core.model.project;

import com.fasterxml.jackson.annotation.JsonBackReference;
import jakarta.persistence.*;
import org.rostilos.codecrow.core.model.vcs.EVcsProvider;
import org.rostilos.codecrow.core.model.vcs.VcsConnection;
import org.rostilos.codecrow.core.model.vcs.VcsRepoBinding;
import org.rostilos.codecrow.core.model.vcs.VcsRepoInfo;

import java.util.UUID;

/**
 * @deprecated Use {@link VcsRepoBinding} instead. This class is Bitbucket-specific and lacks
 *             webhook tracking, timestamps, and provider-agnostic repository ID support.
 *             <p>
 *             Migration: Use {@link Project#getEffectiveVcsRepoInfo()} which returns {@link VcsRepoInfo}
 *             and handles both legacy and new bindings transparently.
 */
@Deprecated(since = "2.0", forRemoval = true)
@Entity
@Table(name = "project_vcs_connection", uniqueConstraints = {
        @UniqueConstraint(name = "uq_repo_unique", columnNames = {"repository_id"})
})
public class ProjectVcsConnectionBinding implements VcsRepoInfo {

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

    @Override
    public String getRepoWorkspace() {
        return workspace;
    }

    public String getWorkspace() {
        return workspace;
    }

    public void setWorkspace(String workspace) {
        this.workspace = workspace;
    }

    @Override
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

    @Override
    public VcsConnection getVcsConnection() {
        return vcsConnection;
    }

    public void setVcsConnection(VcsConnection vcsConnection) {
        this.vcsConnection = vcsConnection;
    }
}
