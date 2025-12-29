package org.rostilos.codecrow.analysisengine.dto.request.processor;

import jakarta.validation.constraints.NotBlank;
import jakarta.validation.constraints.NotNull;
import org.rostilos.codecrow.core.model.codeanalysis.AnalysisType;

public class BranchProcessRequest implements AnalysisProcessRequest {
    @NotNull(message = "Project ID is required")
    public Long projectId;

    @NotBlank(message = "Target branch name is required")
    public String targetBranchName;

    @NotBlank(message = "Commit hash is required")
    public String commitHash;

    @NotBlank(message = "Specify analysis type")
    public AnalysisType analysisType;

    /**
     * Optional: The PR number that triggered this branch analysis (for pullrequest:fulfilled events).
     * When provided, the PR diff will be used instead of commit diff to get ALL changed files.
     * This ensures all files from the original PR are analyzed, not just merge commit changes.
     */
    public Long sourcePrNumber;

    /**
     * Optional: ZIP archive of the repository for first-time full indexing in RAG pipeline.
     * If provided, the entire repository will be indexed.
     * If not provided, only incremental updates will be performed.
     */
    public byte[] archive;

    public Long getProjectId() {
        return projectId;
    }

    public String getTargetBranchName() {
        return targetBranchName;
    }

    public String getCommitHash() {
        return commitHash;
    }

    public AnalysisType getAnalysisType() { return analysisType; }

    public Long getSourcePrNumber() { return sourcePrNumber; }

    public byte[] getArchive() {
        return archive;
    }

    public void setArchive(byte[] archive) {
        this.archive = archive;
    }
}
