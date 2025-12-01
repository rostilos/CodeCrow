package org.rostilos.codecrow.pipelineagent.generic.dto.request.processor;

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

    public byte[] getArchive() {
        return archive;
    }

    public void setArchive(byte[] archive) {
        this.archive = archive;
    }
}
