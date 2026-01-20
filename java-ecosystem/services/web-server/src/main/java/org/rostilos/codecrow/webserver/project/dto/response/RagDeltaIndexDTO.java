package org.rostilos.codecrow.webserver.project.dto.response;

import org.rostilos.codecrow.core.model.rag.DeltaIndexStatus;
import org.rostilos.codecrow.core.model.rag.RagDeltaIndex;

import java.time.OffsetDateTime;

/**
 * DTO for RAG delta index information.
 */
public class RagDeltaIndexDTO {
    
    private Long id;
    private String branchName;
    private String baseBranch;
    private String baseCommitHash;
    private String deltaCommitHash;
    private DeltaIndexStatus status;
    private Integer chunkCount;
    private Integer fileCount;
    private String errorMessage;
    private OffsetDateTime createdAt;
    private OffsetDateTime updatedAt;
    private OffsetDateTime lastAccessedAt;

    public RagDeltaIndexDTO() {
    }

    public static RagDeltaIndexDTO fromEntity(RagDeltaIndex entity) {
        if (entity == null) return null;
        
        RagDeltaIndexDTO dto = new RagDeltaIndexDTO();
        dto.setId(entity.getId());
        dto.setBranchName(entity.getBranchName());
        dto.setBaseBranch(entity.getBaseBranch());
        dto.setBaseCommitHash(entity.getBaseCommitHash());
        dto.setDeltaCommitHash(entity.getDeltaCommitHash());
        dto.setStatus(entity.getStatus());
        dto.setChunkCount(entity.getChunkCount());
        dto.setFileCount(entity.getFileCount());
        dto.setErrorMessage(entity.getErrorMessage());
        dto.setCreatedAt(entity.getCreatedAt());
        dto.setUpdatedAt(entity.getUpdatedAt());
        dto.setLastAccessedAt(entity.getLastAccessedAt());
        return dto;
    }

    // Getters and Setters
    public Long getId() {
        return id;
    }

    public void setId(Long id) {
        this.id = id;
    }

    public String getBranchName() {
        return branchName;
    }

    public void setBranchName(String branchName) {
        this.branchName = branchName;
    }

    public String getBaseBranch() {
        return baseBranch;
    }

    public void setBaseBranch(String baseBranch) {
        this.baseBranch = baseBranch;
    }

    public String getBaseCommitHash() {
        return baseCommitHash;
    }

    public void setBaseCommitHash(String baseCommitHash) {
        this.baseCommitHash = baseCommitHash;
    }

    public String getDeltaCommitHash() {
        return deltaCommitHash;
    }

    public void setDeltaCommitHash(String deltaCommitHash) {
        this.deltaCommitHash = deltaCommitHash;
    }

    public DeltaIndexStatus getStatus() {
        return status;
    }

    public void setStatus(DeltaIndexStatus status) {
        this.status = status;
    }

    public Integer getChunkCount() {
        return chunkCount;
    }

    public void setChunkCount(Integer chunkCount) {
        this.chunkCount = chunkCount;
    }

    public Integer getFileCount() {
        return fileCount;
    }

    public void setFileCount(Integer fileCount) {
        this.fileCount = fileCount;
    }

    public String getErrorMessage() {
        return errorMessage;
    }

    public void setErrorMessage(String errorMessage) {
        this.errorMessage = errorMessage;
    }

    public OffsetDateTime getCreatedAt() {
        return createdAt;
    }

    public void setCreatedAt(OffsetDateTime createdAt) {
        this.createdAt = createdAt;
    }

    public OffsetDateTime getUpdatedAt() {
        return updatedAt;
    }

    public void setUpdatedAt(OffsetDateTime updatedAt) {
        this.updatedAt = updatedAt;
    }

    public OffsetDateTime getLastAccessedAt() {
        return lastAccessedAt;
    }

    public void setLastAccessedAt(OffsetDateTime lastAccessedAt) {
        this.lastAccessedAt = lastAccessedAt;
    }
}
