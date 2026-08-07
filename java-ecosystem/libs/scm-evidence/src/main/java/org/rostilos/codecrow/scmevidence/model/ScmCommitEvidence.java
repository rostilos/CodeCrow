package org.rostilos.codecrow.scmevidence.model;

import jakarta.persistence.*;

import java.time.OffsetDateTime;
import java.util.ArrayList;
import java.util.List;

@Entity
@Table(name = "scm_commit_evidence", uniqueConstraints = @UniqueConstraint(
        name = "uq_scm_commit_evidence_project_hash",
        columnNames = {"project_id", "commit_hash"}))
public class ScmCommitEvidence {
    @Id @GeneratedValue(strategy = GenerationType.IDENTITY)
    private Long id;
    @Column(name = "project_id", nullable = false)
    private Long projectId;
    @Column(name = "commit_hash", nullable = false, length = 64)
    private String commitHash;
    @Column(name = "patch_id", nullable = false, length = 64)
    private String patchId;
    @Column(name = "author_name", length = 200)
    private String authorName;
    @Column(name = "author_email", length = 320)
    private String authorEmail;
    @Column(name = "captured_at", nullable = false)
    private OffsetDateTime capturedAt = OffsetDateTime.now();
    @OneToMany(mappedBy = "commitEvidence", cascade = CascadeType.ALL,
            orphanRemoval = true)
    private List<ScmAddedLineEvidence> addedLines = new ArrayList<>();

    public ScmCommitEvidence() {}

    public ScmCommitEvidence(Long projectId, String commitHash, String patchId,
                             String authorName, String authorEmail) {
        this.projectId = projectId;
        this.commitHash = commitHash;
        this.patchId = patchId;
        this.authorName = authorName;
        this.authorEmail = authorEmail;
    }

    public void addLine(ScmAddedLineEvidence line) {
        line.setCommitEvidence(this);
        addedLines.add(line);
    }

    public Long getId() { return id; }
    public Long getProjectId() { return projectId; }
    public String getCommitHash() { return commitHash; }
    public String getPatchId() { return patchId; }
    public String getAuthorName() { return authorName; }
    public String getAuthorEmail() { return authorEmail; }
    public OffsetDateTime getCapturedAt() { return capturedAt; }
    public List<ScmAddedLineEvidence> getAddedLines() { return addedLines; }
}
