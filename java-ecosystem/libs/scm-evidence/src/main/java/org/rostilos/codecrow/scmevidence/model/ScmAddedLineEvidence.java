package org.rostilos.codecrow.scmevidence.model;

import jakarta.persistence.*;

@Entity
@Table(name = "scm_added_line_evidence", indexes = @Index(
        name = "idx_scm_added_line_lookup",
        columnList = "project_id,file_path,line_hash"))
public class ScmAddedLineEvidence {
    @Id @GeneratedValue(strategy = GenerationType.IDENTITY)
    private Long id;
    @ManyToOne(fetch = FetchType.LAZY, optional = false)
    @JoinColumn(name = "commit_evidence_id", nullable = false)
    private ScmCommitEvidence commitEvidence;
    @Column(name = "project_id", nullable = false)
    private Long projectId;
    @Column(name = "file_path", nullable = false, length = 1024)
    private String filePath;
    @Column(name = "new_line_number", nullable = false)
    private int newLineNumber;
    @Column(name = "line_hash", nullable = false, length = 64)
    private String lineHash;

    public ScmAddedLineEvidence() {}

    public ScmAddedLineEvidence(Long projectId, String filePath,
                                int newLineNumber, String lineHash) {
        this.projectId = projectId;
        this.filePath = filePath;
        this.newLineNumber = newLineNumber;
        this.lineHash = lineHash;
    }

    public void setCommitEvidence(ScmCommitEvidence commitEvidence) {
        this.commitEvidence = commitEvidence;
    }
    public ScmCommitEvidence getCommitEvidence() { return commitEvidence; }
    public String getFilePath() { return filePath; }
    public int getNewLineNumber() { return newLineNumber; }
    public String getLineHash() { return lineHash; }
}
