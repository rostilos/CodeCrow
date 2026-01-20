package org.rostilos.codecrow.analysisengine.dto.request.processor;

import org.junit.jupiter.api.DisplayName;
import org.junit.jupiter.api.Nested;
import org.junit.jupiter.api.Test;
import org.rostilos.codecrow.core.model.codeanalysis.AnalysisType;

import static org.assertj.core.api.Assertions.assertThat;

@DisplayName("PrProcessRequest")
class PrProcessRequestTest {

    @Nested
    @DisplayName("Getters")
    class Getters {

        @Test
        @DisplayName("should get projectId")
        void shouldGetProjectId() {
            PrProcessRequest request = new PrProcessRequest();
            request.projectId = 42L;
            
            assertThat(request.getProjectId()).isEqualTo(42L);
        }

        @Test
        @DisplayName("should get pullRequestId")
        void shouldGetPullRequestId() {
            PrProcessRequest request = new PrProcessRequest();
            request.pullRequestId = 123L;
            
            assertThat(request.getPullRequestId()).isEqualTo(123L);
        }

        @Test
        @DisplayName("should get targetBranchName")
        void shouldGetTargetBranchName() {
            PrProcessRequest request = new PrProcessRequest();
            request.targetBranchName = "main";
            
            assertThat(request.getTargetBranchName()).isEqualTo("main");
        }

        @Test
        @DisplayName("should get sourceBranchName")
        void shouldGetSourceBranchName() {
            PrProcessRequest request = new PrProcessRequest();
            request.sourceBranchName = "feature/new-feature";
            
            assertThat(request.getSourceBranchName()).isEqualTo("feature/new-feature");
        }

        @Test
        @DisplayName("should get commitHash")
        void shouldGetCommitHash() {
            PrProcessRequest request = new PrProcessRequest();
            request.commitHash = "abc123def456";
            
            assertThat(request.getCommitHash()).isEqualTo("abc123def456");
        }

        @Test
        @DisplayName("should get analysisType")
        void shouldGetAnalysisType() {
            PrProcessRequest request = new PrProcessRequest();
            request.analysisType = AnalysisType.PR_REVIEW;
            
            assertThat(request.getAnalysisType()).isEqualTo(AnalysisType.PR_REVIEW);
        }

        @Test
        @DisplayName("should get placeholderCommentId")
        void shouldGetPlaceholderCommentId() {
            PrProcessRequest request = new PrProcessRequest();
            request.placeholderCommentId = "comment-123";
            
            assertThat(request.getPlaceholderCommentId()).isEqualTo("comment-123");
        }
    }

    @Nested
    @DisplayName("Default Values")
    class DefaultValues {

        @Test
        @DisplayName("should have null values by default")
        void shouldHaveNullValuesByDefault() {
            PrProcessRequest request = new PrProcessRequest();
            
            assertThat(request.getProjectId()).isNull();
            assertThat(request.getPullRequestId()).isNull();
            assertThat(request.getTargetBranchName()).isNull();
            assertThat(request.getSourceBranchName()).isNull();
            assertThat(request.getCommitHash()).isNull();
            assertThat(request.getAnalysisType()).isNull();
            assertThat(request.getPlaceholderCommentId()).isNull();
        }
    }

    @Nested
    @DisplayName("AnalysisProcessRequest interface")
    class AnalysisProcessRequestInterface {

        @Test
        @DisplayName("should implement AnalysisProcessRequest")
        void shouldImplementAnalysisProcessRequest() {
            PrProcessRequest request = new PrProcessRequest();
            
            assertThat(request).isInstanceOf(AnalysisProcessRequest.class);
        }
    }
}
