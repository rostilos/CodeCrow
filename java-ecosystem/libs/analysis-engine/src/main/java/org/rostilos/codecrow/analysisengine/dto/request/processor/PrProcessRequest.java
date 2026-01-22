package org.rostilos.codecrow.analysisengine.dto.request.processor;

import com.fasterxml.jackson.annotation.JsonInclude;
import jakarta.validation.constraints.NotBlank;
import jakarta.validation.constraints.NotNull;
import org.rostilos.codecrow.core.model.codeanalysis.AnalysisType;
import org.rostilos.codecrow.analysisengine.dto.request.validation.ValidWebhookRequest;

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
    
    /**
     * Optional placeholder comment ID for updating an existing comment with results.
     * If set, the analysis result will be posted by updating this comment instead of creating a new one.
     */
    public String placeholderCommentId;

    public String prAuthorId;

    public String prAuthorUsername;


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
    
    public String getPlaceholderCommentId() { return placeholderCommentId; }

    public String getPrAuthorId() { return prAuthorId; }

    public String getPrAuthorUsername() { return prAuthorUsername; }
}
