package org.rostilos.codecrow.pipelineagent.bitbucket.dto.request.ai;

import org.rostilos.codecrow.core.model.ai.AIConnection;
import org.rostilos.codecrow.core.model.codeanalysis.AnalysisType;
import org.rostilos.codecrow.core.model.codeanalysis.CodeAnalysis;
import org.rostilos.codecrow.core.model.project.ProjectVcsConnectionBinding;
import org.rostilos.codecrow.pipelineagent.generic.dto.request.ai.AiAnalysisRequestImpl;

import java.util.List;
import java.util.Optional;

public class AiPullRequestAnalysisRequest extends AiAnalysisRequestImpl
{
    protected AiPullRequestAnalysisRequest(Builder builder) {
        super(builder);
    }

    public static Builder builder() {
        return new Builder();
    }

    public static class Builder extends AiAnalysisRequestImpl.Builder {

        protected Builder() {
            super();
        }

        @Override
        public Builder withProjectId(Long projectId) {
            super.withProjectId(projectId);
            return this;
        }

        @Override
        public Builder withPullRequestId(Long pullRequestId) {
            super.withPullRequestId(pullRequestId);
            return this;
        }

        @Override
        public Builder withProjectAiConnection(AIConnection projectAiConnection) {
            super.withProjectAiConnection(projectAiConnection);
            return this;
        }

        @Override
        public Builder withProjectVcsConnectionBinding(ProjectVcsConnectionBinding projectVcsConnectionBinding) {
            super.withProjectVcsConnectionBinding(projectVcsConnectionBinding);
            return this;
        }

        @Override
        public Builder withProjectVcsConnectionBindingInfo(String workspace, String repoSlug) {
            super.withProjectVcsConnectionBindingInfo(workspace, repoSlug);
            return this;
        }

        @Override
        public Builder withProjectAiConnectionTokenDecrypted(String decryptedToken) {
            super.withProjectAiConnectionTokenDecrypted(decryptedToken);
            return this;
        }

        @Override
        public Builder withProjectVcsConnectionCredentials(String oAuthClient, String oAuthSecret) {
            super.withProjectVcsConnectionCredentials(oAuthClient, oAuthSecret);
            return this;
        }

        @Override
        public Builder withAccessToken(String accessToken) {
            super.withAccessToken(accessToken);
            return this;
        }

        @Override
        public Builder withPreviousAnalysisData(Optional<CodeAnalysis> optionalPreviousAnalysis) {
            super.withPreviousAnalysisData(optionalPreviousAnalysis);
            return this;
        }

        @Override
        public Builder withMaxAllowedTokens(int maxAllowedTokens) {
            super.withMaxAllowedTokens(maxAllowedTokens);
            return this;
        }

        @Override
        public Builder withUseLocalMcp(boolean useLocalMcp) {
            super.withUseLocalMcp(useLocalMcp);
            return this;
        }

        @Override
        public Builder withAnalysisType(AnalysisType analysisType) {
            super.withAnalysisType(analysisType);
            return this;
        }

        @Override
        public Builder withPrTitle(String prTitle) {
            super.withPrTitle(prTitle);
            return this;
        }

        @Override
        public Builder withPrDescription(String prDescription) {
            super.withPrDescription(prDescription);
            return this;
        }

        @Override
        public Builder withChangedFiles(List<String> changedFiles) {
            super.withChangedFiles(changedFiles);
            return this;
        }

        @Override
        public Builder withDiffSnippets(List<String> diffSnippets) {
            super.withDiffSnippets(diffSnippets);
            return this;
        }

        @Override
        public Builder withProjectMetadata(String workspace, String namespace) {
            super.withProjectMetadata(workspace, namespace);
            return this;
        }

        @Override
        public Builder withTargetBranchName(String targetBranchName) {
            super.withTargetBranchName(targetBranchName);
            return this;
        }

        @Override
        public Builder withVcsProvider(String vcsProvider) {
            super.withVcsProvider(vcsProvider);
            return this;
        }

        @Override
        public AiPullRequestAnalysisRequest build() {
            return new AiPullRequestAnalysisRequest(this);
        }
    }
}
