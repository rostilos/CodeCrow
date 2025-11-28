package org.rostilos.codecrow.pipelineagent.bitbucket.dto.request.ai;

import org.rostilos.codecrow.core.model.ai.AIConnection;
import org.rostilos.codecrow.core.model.codeanalysis.AnalysisType;
import org.rostilos.codecrow.core.model.codeanalysis.CodeAnalysis;
import org.rostilos.codecrow.core.model.project.ProjectVcsConnectionBinding;
import org.rostilos.codecrow.pipelineagent.generic.dto.request.ai.AiAnalysisRequestImpl;

import java.util.Optional;

public class AiBranchAnalysisRequest extends AiAnalysisRequestImpl
{
    protected final String branch;
    protected final String commitHash;

    protected AiBranchAnalysisRequest(Builder builder) {
        super(builder);
        this.branch = builder.branch;
        this.commitHash = builder.commitHash;
    }

    public String getBranch() {
        return branch;
    }

    public String getCommitHash() {
        return commitHash;
    }

    public static Builder builder() {
        return new Builder();
    }

    public static class Builder extends AiAnalysisRequestImpl.Builder {
        protected String branch;
        protected String commitHash;

        protected Builder() {
            super();
        }

        public Builder withBranch(String branch) {
            this.branch = branch;
            return this;
        }

        public Builder withCommitHash(String commitHash) {
            this.commitHash = commitHash;
            return this;
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
        public AiBranchAnalysisRequest build() {
            return new AiBranchAnalysisRequest(this);
        }
    }
}
