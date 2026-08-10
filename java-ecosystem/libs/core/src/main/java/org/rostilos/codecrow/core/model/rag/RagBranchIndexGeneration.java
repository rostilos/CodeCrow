package org.rostilos.codecrow.core.model.rag;

import jakarta.persistence.*;

import java.time.OffsetDateTime;

/**
 * One immutable physical representation of a branch at an exact repository
 * revision. A generation is published by pointing its owning branch index at it;
 * failed or incomplete generations never replace the active generation.
 */
@Entity
@Table(name = "rag_branch_index_generation", indexes = {
        @Index(name = "idx_rag_branch_generation_revision", columnList = "branch_index_id, revision"),
        @Index(name = "idx_rag_branch_generation_status", columnList = "branch_index_id, status")
})
public class RagBranchIndexGeneration {

    @Id
    @GeneratedValue(strategy = GenerationType.IDENTITY)
    private Long id;

    @ManyToOne(fetch = FetchType.LAZY, optional = false)
    @JoinColumn(name = "branch_index_id", nullable = false)
    private RagBranchIndex branchIndex;

    @Column(name = "revision", nullable = false, length = 64, updatable = false)
    private String revision;

    @ManyToOne(fetch = FetchType.LAZY)
    @JoinColumn(name = "parent_generation_id", updatable = false)
    private RagBranchIndexGeneration parentGeneration;

    @Column(name = "seed_revision", length = 64, updatable = false)
    private String seedRevision;

    @Column(name = "collection_name", nullable = false, length = 300, updatable = false)
    private String collectionName;

    @Enumerated(EnumType.STRING)
    @Column(name = "status", nullable = false, length = 24)
    private RagBranchIndexGenerationStatus status = RagBranchIndexGenerationStatus.BUILDING;

    @Column(name = "manifest_digest", length = 128)
    private String manifestDigest;

    @Column(name = "representation_fingerprint", length = 128, updatable = false)
    private String representationFingerprint;

    @Column(name = "file_count")
    private Integer fileCount;

    @Column(name = "chunk_count")
    private Integer chunkCount;

    @Column(name = "created_at", nullable = false, updatable = false)
    private OffsetDateTime createdAt = OffsetDateTime.now();

    @Column(name = "activated_at")
    private OffsetDateTime activatedAt;

    @Column(name = "superseded_at")
    private OffsetDateTime supersededAt;

    @Column(name = "error_message", columnDefinition = "TEXT")
    private String errorMessage;

    public RagBranchIndexGeneration() {
    }

    public RagBranchIndexGeneration(
            RagBranchIndex branchIndex,
            String revision,
            String collectionName,
            RagBranchIndexGeneration parentGeneration,
            String seedRevision,
            String representationFingerprint) {
        this.branchIndex = branchIndex;
        this.revision = revision;
        this.collectionName = collectionName;
        this.parentGeneration = parentGeneration;
        this.seedRevision = seedRevision;
        this.representationFingerprint = representationFingerprint;
    }

    public void activate(String manifestDigest, int fileCount, int chunkCount) {
        this.manifestDigest = manifestDigest;
        this.fileCount = fileCount;
        this.chunkCount = chunkCount;
        this.status = RagBranchIndexGenerationStatus.ACTIVE;
        this.activatedAt = OffsetDateTime.now();
        this.errorMessage = null;
    }

    public void supersede() {
        this.status = RagBranchIndexGenerationStatus.SUPERSEDED;
        this.supersededAt = OffsetDateTime.now();
    }

    public void fail(String errorMessage) {
        this.status = RagBranchIndexGenerationStatus.FAILED;
        this.errorMessage = errorMessage;
    }

    /** Reopen an unpublished failed generation for an idempotent retry. */
    public void retry() {
        if (status != RagBranchIndexGenerationStatus.FAILED) {
            throw new IllegalStateException("Only a failed generation can be retried");
        }
        status = RagBranchIndexGenerationStatus.BUILDING;
        errorMessage = null;
        activatedAt = null;
        supersededAt = null;
        manifestDigest = null;
        fileCount = null;
        chunkCount = null;
    }

    public Long getId() { return id; }
    public void setId(Long id) { this.id = id; }
    public RagBranchIndex getBranchIndex() { return branchIndex; }
    public void setBranchIndex(RagBranchIndex branchIndex) { this.branchIndex = branchIndex; }
    public String getRevision() { return revision; }
    public void setRevision(String revision) { this.revision = revision; }
    public RagBranchIndexGeneration getParentGeneration() { return parentGeneration; }
    public void setParentGeneration(RagBranchIndexGeneration parentGeneration) { this.parentGeneration = parentGeneration; }
    public String getSeedRevision() { return seedRevision; }
    public void setSeedRevision(String seedRevision) { this.seedRevision = seedRevision; }
    public String getCollectionName() { return collectionName; }
    public void setCollectionName(String collectionName) { this.collectionName = collectionName; }
    public RagBranchIndexGenerationStatus getStatus() { return status; }
    public void setStatus(RagBranchIndexGenerationStatus status) { this.status = status; }
    public String getManifestDigest() { return manifestDigest; }
    public void setManifestDigest(String manifestDigest) { this.manifestDigest = manifestDigest; }
    public String getRepresentationFingerprint() { return representationFingerprint; }
    public void setRepresentationFingerprint(String representationFingerprint) { this.representationFingerprint = representationFingerprint; }
    public Integer getFileCount() { return fileCount; }
    public void setFileCount(Integer fileCount) { this.fileCount = fileCount; }
    public Integer getChunkCount() { return chunkCount; }
    public void setChunkCount(Integer chunkCount) { this.chunkCount = chunkCount; }
    public OffsetDateTime getCreatedAt() { return createdAt; }
    public void setCreatedAt(OffsetDateTime createdAt) { this.createdAt = createdAt; }
    public OffsetDateTime getActivatedAt() { return activatedAt; }
    public void setActivatedAt(OffsetDateTime activatedAt) { this.activatedAt = activatedAt; }
    public OffsetDateTime getSupersededAt() { return supersededAt; }
    public void setSupersededAt(OffsetDateTime supersededAt) { this.supersededAt = supersededAt; }
    public String getErrorMessage() { return errorMessage; }
    public void setErrorMessage(String errorMessage) { this.errorMessage = errorMessage; }
}
