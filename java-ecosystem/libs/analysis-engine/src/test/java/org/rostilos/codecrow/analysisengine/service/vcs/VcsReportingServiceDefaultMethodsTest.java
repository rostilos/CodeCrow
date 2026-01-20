package org.rostilos.codecrow.analysisengine.service.vcs;

import org.junit.jupiter.api.BeforeEach;
import org.junit.jupiter.api.DisplayName;
import org.junit.jupiter.api.Test;
import org.rostilos.codecrow.core.model.codeanalysis.CodeAnalysis;
import org.rostilos.codecrow.core.model.project.Project;
import org.rostilos.codecrow.core.model.vcs.EVcsProvider;

import java.io.IOException;

import static org.assertj.core.api.Assertions.assertThat;
import static org.assertj.core.api.Assertions.assertThatThrownBy;
import static org.mockito.Mockito.mock;

/**
 * Tests for default methods in VcsReportingService interface.
 */
@DisplayName("VcsReportingService Default Methods")
class VcsReportingServiceDefaultMethodsTest {

    private VcsReportingService service;
    private Project mockProject;

    @BeforeEach
    void setUp() {
        // Create a minimal implementation that only implements required abstract methods
        service = new VcsReportingService() {
            @Override
            public EVcsProvider getProvider() {
                return EVcsProvider.BITBUCKET_CLOUD;
            }

            @Override
            public void postAnalysisResults(CodeAnalysis codeAnalysis, Project project, 
                    Long pullRequestNumber, Long platformPrEntityId) throws IOException {
                // Minimal implementation
            }
        };
        mockProject = mock(Project.class);
    }

    @Test
    @DisplayName("postAnalysisResults with placeholderCommentId should delegate to base method")
    void postAnalysisResultsWithPlaceholderShouldDelegateToBase() throws IOException {
        CodeAnalysis mockAnalysis = mock(CodeAnalysis.class);
        // Should not throw - delegates to the abstract method which has a minimal impl
        service.postAnalysisResults(mockAnalysis, mockProject, 1L, 1L, "placeholder-id");
    }

    @Test
    @DisplayName("postComment should throw UnsupportedOperationException by default")
    void postCommentShouldThrowUnsupportedOperationException() {
        assertThatThrownBy(() -> service.postComment(mockProject, 1L, "content", "marker"))
                .isInstanceOf(UnsupportedOperationException.class)
                .hasMessageContaining("postComment not implemented");
    }

    @Test
    @DisplayName("postCommentReply should throw UnsupportedOperationException by default")
    void postCommentReplyShouldThrowUnsupportedOperationException() {
        assertThatThrownBy(() -> service.postCommentReply(mockProject, 1L, "parent-id", "content"))
                .isInstanceOf(UnsupportedOperationException.class)
                .hasMessageContaining("postCommentReply not implemented");
    }

    @Test
    @DisplayName("deleteCommentsByMarker should throw UnsupportedOperationException by default")
    void deleteCommentsByMarkerShouldThrowUnsupportedOperationException() {
        assertThatThrownBy(() -> service.deleteCommentsByMarker(mockProject, 1L, "marker"))
                .isInstanceOf(UnsupportedOperationException.class)
                .hasMessageContaining("deleteCommentsByMarker not implemented");
    }

    @Test
    @DisplayName("deleteComment should throw UnsupportedOperationException by default")
    void deleteCommentShouldThrowUnsupportedOperationException() {
        assertThatThrownBy(() -> service.deleteComment(mockProject, 1L, "comment-id"))
                .isInstanceOf(UnsupportedOperationException.class)
                .hasMessageContaining("deleteComment not implemented");
    }

    @Test
    @DisplayName("updateComment should throw UnsupportedOperationException by default")
    void updateCommentShouldThrowUnsupportedOperationException() {
        assertThatThrownBy(() -> service.updateComment(mockProject, 1L, "comment-id", "new content", "marker"))
                .isInstanceOf(UnsupportedOperationException.class)
                .hasMessageContaining("updateComment not implemented");
    }

    @Test
    @DisplayName("postCommentReplyWithContext should fall back to postCommentReply")
    void postCommentReplyWithContextShouldFallBackToBasicReply() {
        // Since postCommentReply throws UnsupportedOperationException, 
        // postCommentReplyWithContext should also throw
        assertThatThrownBy(() -> service.postCommentReplyWithContext(
                mockProject, 1L, "parent-id", "content", "author", "original body"))
                .isInstanceOf(UnsupportedOperationException.class)
                .hasMessageContaining("postCommentReply not implemented");
    }

    @Test
    @DisplayName("supportsMermaidDiagrams should return false by default")
    void supportsMermaidDiagramsShouldReturnFalseByDefault() {
        assertThat(service.supportsMermaidDiagrams()).isFalse();
    }

    @Test
    @DisplayName("deletePreviousCommentsByType should return 0 by default")
    void deletePreviousCommentsByTypeShouldReturnZeroByDefault() throws IOException {
        int result = service.deletePreviousCommentsByType(mockProject, 1L, "summarize");
        assertThat(result).isZero();
    }
}
