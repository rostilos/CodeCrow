package org.rostilos.codecrow.analysisengine.dto.request.processor;

import com.fasterxml.jackson.annotation.JsonInclude;
import jakarta.validation.constraints.NotBlank;
import jakarta.validation.constraints.NotNull;
import org.rostilos.codecrow.core.model.codeanalysis.AnalysisType;
import org.rostilos.codecrow.analysisengine.dto.request.validation.ValidWebhookRequest;

/**
 * Request DTO for PR reconciliation job.
 * This job analyzes a PR diff against existing branch issues to identify
 * issues that could potentially be resolved by the PR.
 * 
 * Unlike PR_ANALYSIS which finds new issues, PR_RECONCILIATION checks if
 * existing issues on the target branch might be fixed by the PR changes.
 * Results are posted as a PR comment (not as a report).
 * 
 * Branch state is NOT modified until the PR is actually merged.
 */
@JsonInclude(JsonInclude.Include.NON_NULL)
@ValidWebhookRequest
public class PrReconciliationRequest implements AnalysisProcessRequest {
    
    @NotNull(message = "Project ID is required")
    public Long projectId;

    @NotNull(message = "Pull request ID is required")
    public Long pullRequestId;

    @NotBlank(message = "Target branch name is required")
    public String targetBranchName;

    @NotBlank(message = "Source branch name is required")
    public String sourceBranchName;

    @NotBlank(message = "Commit hash is required")
    public String commitHash;

    /**
     * Analysis type - should be PR_RECONCILIATION for this request type.
     */
    public AnalysisType analysisType = AnalysisType.PR_RECONCILIATION;
    
    /**
     * Optional placeholder comment ID for updating an existing comment with results.
     */
    public String placeholderCommentId;

    // Getters
    
    @Override
    public Long getProjectId() {
        return projectId;
    }

    public Long getPullRequestId() {
        return pullRequestId;
    }

    @Override
    public String getTargetBranchName() {
        return targetBranchName;
    }

    public String getSourceBranchName() {
        return sourceBranchName;
    }

    @Override
    public String getCommitHash() {
        return commitHash;
    }

    @Override
    public AnalysisType getAnalysisType() {
        return analysisType;
    }
    
    public String getPlaceholderCommentId() {
        return placeholderCommentId;
    }
}
