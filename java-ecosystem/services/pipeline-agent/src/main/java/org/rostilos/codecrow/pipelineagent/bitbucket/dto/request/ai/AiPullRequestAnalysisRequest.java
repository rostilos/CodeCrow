package org.rostilos.codecrow.pipelineagent.bitbucket.dto.request.ai;

import org.rostilos.codecrow.analysisengine.dto.request.ai.AiAnalysisRequestImpl;

/**
 * Bitbucket-specific PR analysis request.
 * Extends the generic AiAnalysisRequestImpl with the same structure.
 */
public class AiPullRequestAnalysisRequest extends AiAnalysisRequestImpl {
    
    protected AiPullRequestAnalysisRequest(Builder builder) {
        super(builder);
    }

    public static Builder builder() {
        return new Builder();
    }

    public static class Builder extends AiAnalysisRequestImpl.Builder<Builder> {

        protected Builder() {
            super();
        }

        @Override
        protected Builder self() {
            return this;
        }

        @Override
        public AiPullRequestAnalysisRequest build() {
            return new AiPullRequestAnalysisRequest(this);
        }
    }
}
