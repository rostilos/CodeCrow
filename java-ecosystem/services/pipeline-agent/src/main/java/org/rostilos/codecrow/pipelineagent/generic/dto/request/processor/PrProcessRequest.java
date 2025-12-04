package org.rostilos.codecrow.pipelineagent.generic.dto.request.processor;

import com.fasterxml.jackson.annotation.JsonInclude;
import jakarta.validation.constraints.NotBlank;
import jakarta.validation.constraints.NotNull;
import org.rostilos.codecrow.core.model.codeanalysis.AnalysisType;
import org.rostilos.codecrow.pipelineagent.generic.dto.request.validation.ValidWebhookRequest;

@JsonInclude(JsonInclude.Include.NON_NULL)
@ValidWebhookRequest
public class PrProcessRequest implements AnalysisProcessRequest {
    @NotNull(message = "Project ID is required")
    public Long projectId;

    public Long pullRequestId;

    @NotBlank(message = "Target branch name is required")
    public String targetBranchName;

    @NotBlank(message = "Source branch name is required")
    public String sourceBranchName;

    @NotBlank(message = "Commit hash is required")
    public String commitHash;

    @NotBlank(message = "Specify analysis type")
    public AnalysisType analysisType;


    public Long getProjectId() {
        return projectId;
    }

    public Long getPullRequestId() {
        return pullRequestId;
    }

    public String getTargetBranchName() {
        return targetBranchName;
    }

    public String getCommitHash() {
        return commitHash;
    }

    public String getSourceBranchName() {
        return sourceBranchName;
    }

    public AnalysisType getAnalysisType() { return analysisType; }
}
