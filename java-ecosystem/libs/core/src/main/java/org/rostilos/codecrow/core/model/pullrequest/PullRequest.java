package org.rostilos.codecrow.core.model.pullrequest;

import jakarta.persistence.*;
import org.rostilos.codecrow.core.model.project.Project;

//TODO: uniq project_id - pull_request_id + uniq commit_hash ( by platform maybe )
@Entity
@Table(
        name = "pull_request",
        uniqueConstraints = {
                @UniqueConstraint(
                        name = "uq_pull_request_project_id_id",
                        columnNames = {"project_id", "id"}
                )
        }
)
public class PullRequest {
    @Id
    @GeneratedValue(strategy = GenerationType.IDENTITY)
    @Column(nullable = false, updatable = false)
    private Long id;

    @Column(name = "pr_number", updatable = false)
    private Long prNumber;

    @Column(name = "commit_hash", length = 40)
    private String commitHash;

    @Column(name = "target_branch_name", length = 40)
    private String targetBranchName;

    @Column(name = "source_branch_name")
    private String sourceBranchName;

    @ManyToOne(fetch = FetchType.LAZY)
    @JoinColumn(name = "project_id", nullable = false)
    private Project project;


    public Long getId() {
        return id;
    }

    public void setId(Long id) {
        this.id = id;
    }

    public Long getPrNumber() { return prNumber; }
    public void setPrNumber(Long prNumber) { this.prNumber = prNumber; }

    public String getCommitHash() {
        return commitHash;
    }
    public void setCommitHash(String commitHash) {
        this.commitHash = commitHash;
    }

    public String getTargetBranchName() {
        return targetBranchName;
    }
    public void setTargetBranchName(String targetBranchName) {
        this.targetBranchName = targetBranchName;
    }

    public String getSourceBranchName() {
        return sourceBranchName;
    }
    public void setSourceBranchName(String sourceBranchName) {
        this.sourceBranchName = sourceBranchName;
    }

    public Project getProject() {
        return project;
    }

    public void setProject(Project project) {
        this.project = project;
    }
}
