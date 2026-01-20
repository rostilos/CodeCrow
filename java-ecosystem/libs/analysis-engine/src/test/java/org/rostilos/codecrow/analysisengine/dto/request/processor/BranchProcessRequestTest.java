package org.rostilos.codecrow.analysisengine.dto.request.processor;

import org.junit.jupiter.api.DisplayName;
import org.junit.jupiter.api.Nested;
import org.junit.jupiter.api.Test;
import org.rostilos.codecrow.core.model.codeanalysis.AnalysisType;

import static org.assertj.core.api.Assertions.assertThat;

@DisplayName("BranchProcessRequest")
class BranchProcessRequestTest {

    @Nested
    @DisplayName("Getters")
    class Getters {

        @Test
        @DisplayName("should get projectId")
        void shouldGetProjectId() {
            BranchProcessRequest request = new BranchProcessRequest();
            request.projectId = 42L;
            
            assertThat(request.getProjectId()).isEqualTo(42L);
        }

        @Test
        @DisplayName("should get targetBranchName")
        void shouldGetTargetBranchName() {
            BranchProcessRequest request = new BranchProcessRequest();
            request.targetBranchName = "main";
            
            assertThat(request.getTargetBranchName()).isEqualTo("main");
        }

        @Test
        @DisplayName("should get commitHash")
        void shouldGetCommitHash() {
            BranchProcessRequest request = new BranchProcessRequest();
            request.commitHash = "abc123def456";
            
            assertThat(request.getCommitHash()).isEqualTo("abc123def456");
        }

        @Test
        @DisplayName("should get analysisType")
        void shouldGetAnalysisType() {
            BranchProcessRequest request = new BranchProcessRequest();
            request.analysisType = AnalysisType.BRANCH_ANALYSIS;
            
            assertThat(request.getAnalysisType()).isEqualTo(AnalysisType.BRANCH_ANALYSIS);
        }

        @Test
        @DisplayName("should get sourcePrNumber")
        void shouldGetSourcePrNumber() {
            BranchProcessRequest request = new BranchProcessRequest();
            request.sourcePrNumber = 123L;
            
            assertThat(request.getSourcePrNumber()).isEqualTo(123L);
        }
    }

    @Nested
    @DisplayName("Archive")
    class Archive {

        @Test
        @DisplayName("should get and set archive")
        void shouldGetAndSetArchive() {
            BranchProcessRequest request = new BranchProcessRequest();
            byte[] archive = new byte[]{1, 2, 3, 4, 5};
            
            request.setArchive(archive);
            
            assertThat(request.getArchive()).isEqualTo(archive);
        }

        @Test
        @DisplayName("should handle null archive")
        void shouldHandleNullArchive() {
            BranchProcessRequest request = new BranchProcessRequest();
            
            assertThat(request.getArchive()).isNull();
        }
    }

    @Nested
    @DisplayName("Default Values")
    class DefaultValues {

        @Test
        @DisplayName("should have null values by default")
        void shouldHaveNullValuesByDefault() {
            BranchProcessRequest request = new BranchProcessRequest();
            
            assertThat(request.getProjectId()).isNull();
            assertThat(request.getTargetBranchName()).isNull();
            assertThat(request.getCommitHash()).isNull();
            assertThat(request.getAnalysisType()).isNull();
            assertThat(request.getSourcePrNumber()).isNull();
            assertThat(request.getArchive()).isNull();
        }
    }

    @Nested
    @DisplayName("AnalysisProcessRequest interface")
    class AnalysisProcessRequestInterface {

        @Test
        @DisplayName("should implement AnalysisProcessRequest")
        void shouldImplementAnalysisProcessRequest() {
            BranchProcessRequest request = new BranchProcessRequest();
            
            assertThat(request).isInstanceOf(AnalysisProcessRequest.class);
        }
    }
}
