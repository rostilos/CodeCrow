package org.rostilos.codecrow.pipelineagent.bitbucket.dto.request.ai;

import org.rostilos.codecrow.analysisengine.dto.request.ai.AiAnalysisRequestImpl;

/**
 * Bitbucket-specific branch analysis request.
 * Extends the generic AiAnalysisRequestImpl with branch-specific fields.
 */
public class AiBranchAnalysisRequest extends AiAnalysisRequestImpl {
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

    public static class Builder extends AiAnalysisRequestImpl.Builder<Builder> {
        protected String branch;
        protected String commitHash;

        protected Builder() {
            super();
        }

        @Override
        protected Builder self() {
            return this;
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
        public AiBranchAnalysisRequest build() {
            return new AiBranchAnalysisRequest(this);
        }
    }
}
