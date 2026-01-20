package org.rostilos.codecrow.core.model.pullrequest;

import org.junit.jupiter.api.BeforeEach;
import org.junit.jupiter.api.DisplayName;
import org.junit.jupiter.api.Nested;
import org.junit.jupiter.api.Test;
import org.rostilos.codecrow.core.model.project.Project;

import static org.assertj.core.api.Assertions.assertThat;
import static org.mockito.Mockito.mock;

@DisplayName("PullRequest Entity Tests")
class PullRequestTest {

    private PullRequest pullRequest;

    @BeforeEach
    void setUp() {
        pullRequest = new PullRequest();
    }

    @Nested
    @DisplayName("Getter and Setter tests")
    class GetterSetterTests {

        @Test
        @DisplayName("Should set and get id")
        void shouldSetAndGetId() {
            pullRequest.setId(100L);
            assertThat(pullRequest.getId()).isEqualTo(100L);
        }

        @Test
        @DisplayName("Should set and get prNumber")
        void shouldSetAndGetPrNumber() {
            pullRequest.setPrNumber(42L);
            assertThat(pullRequest.getPrNumber()).isEqualTo(42L);
        }

        @Test
        @DisplayName("Should set and get commitHash")
        void shouldSetAndGetCommitHash() {
            String hash = "abc123def456789012345678901234567890";
            pullRequest.setCommitHash(hash);
            assertThat(pullRequest.getCommitHash()).isEqualTo(hash);
        }

        @Test
        @DisplayName("Should set and get targetBranchName")
        void shouldSetAndGetTargetBranchName() {
            pullRequest.setTargetBranchName("main");
            assertThat(pullRequest.getTargetBranchName()).isEqualTo("main");
        }

        @Test
        @DisplayName("Should set and get sourceBranchName")
        void shouldSetAndGetSourceBranchName() {
            pullRequest.setSourceBranchName("feature/new-feature");
            assertThat(pullRequest.getSourceBranchName()).isEqualTo("feature/new-feature");
        }

        @Test
        @DisplayName("Should set and get project")
        void shouldSetAndGetProject() {
            Project project = mock(Project.class);
            pullRequest.setProject(project);
            assertThat(pullRequest.getProject()).isSameAs(project);
        }
    }

    @Nested
    @DisplayName("Initial state tests")
    class InitialStateTests {

        @Test
        @DisplayName("New PullRequest should have null id")
        void newPullRequestShouldHaveNullId() {
            assertThat(pullRequest.getId()).isNull();
        }

        @Test
        @DisplayName("New PullRequest should have null prNumber")
        void newPullRequestShouldHaveNullPrNumber() {
            assertThat(pullRequest.getPrNumber()).isNull();
        }

        @Test
        @DisplayName("New PullRequest should have null commitHash")
        void newPullRequestShouldHaveNullCommitHash() {
            assertThat(pullRequest.getCommitHash()).isNull();
        }

        @Test
        @DisplayName("New PullRequest should have null targetBranchName")
        void newPullRequestShouldHaveNullTargetBranchName() {
            assertThat(pullRequest.getTargetBranchName()).isNull();
        }

        @Test
        @DisplayName("New PullRequest should have null sourceBranchName")
        void newPullRequestShouldHaveNullSourceBranchName() {
            assertThat(pullRequest.getSourceBranchName()).isNull();
        }

        @Test
        @DisplayName("New PullRequest should have null project")
        void newPullRequestShouldHaveNullProject() {
            assertThat(pullRequest.getProject()).isNull();
        }
    }

    @Nested
    @DisplayName("Value update tests")
    class ValueUpdateTests {

        @Test
        @DisplayName("Should be able to update all fields")
        void shouldBeAbleToUpdateAllFields() {
            Project project = mock(Project.class);
            
            pullRequest.setId(1L);
            pullRequest.setPrNumber(123L);
            pullRequest.setCommitHash("hash1");
            pullRequest.setTargetBranchName("target1");
            pullRequest.setSourceBranchName("source1");
            pullRequest.setProject(project);

            pullRequest.setId(2L);
            pullRequest.setPrNumber(456L);
            pullRequest.setCommitHash("hash2");
            pullRequest.setTargetBranchName("target2");
            pullRequest.setSourceBranchName("source2");

            assertThat(pullRequest.getId()).isEqualTo(2L);
            assertThat(pullRequest.getPrNumber()).isEqualTo(456L);
            assertThat(pullRequest.getCommitHash()).isEqualTo("hash2");
            assertThat(pullRequest.getTargetBranchName()).isEqualTo("target2");
            assertThat(pullRequest.getSourceBranchName()).isEqualTo("source2");
        }

        @Test
        @DisplayName("Should handle null values on update")
        void shouldHandleNullValuesOnUpdate() {
            pullRequest.setId(1L);
            pullRequest.setPrNumber(123L);
            pullRequest.setCommitHash("hash");
            pullRequest.setTargetBranchName("target");
            pullRequest.setSourceBranchName("source");

            pullRequest.setCommitHash(null);
            pullRequest.setTargetBranchName(null);
            pullRequest.setSourceBranchName(null);
            pullRequest.setProject(null);

            assertThat(pullRequest.getCommitHash()).isNull();
            assertThat(pullRequest.getTargetBranchName()).isNull();
            assertThat(pullRequest.getSourceBranchName()).isNull();
            assertThat(pullRequest.getProject()).isNull();
        }
    }

    @Nested
    @DisplayName("String length validation tests")
    class StringLengthTests {

        @Test
        @DisplayName("Should accept 40-character commit hash")
        void shouldAccept40CharacterCommitHash() {
            String hash40 = "1234567890123456789012345678901234567890";
            pullRequest.setCommitHash(hash40);
            assertThat(pullRequest.getCommitHash()).hasSize(40);
        }

        @Test
        @DisplayName("Should accept long source branch name")
        void shouldAcceptLongSourceBranchName() {
            String longBranchName = "feature/very-long-branch-name-with-many-segments/and-more";
            pullRequest.setSourceBranchName(longBranchName);
            assertThat(pullRequest.getSourceBranchName()).isEqualTo(longBranchName);
        }

        @Test
        @DisplayName("Should accept 40-character target branch name")
        void shouldAccept40CharacterTargetBranchName() {
            String branch40 = "1234567890123456789012345678901234567890";
            pullRequest.setTargetBranchName(branch40);
            assertThat(pullRequest.getTargetBranchName()).hasSize(40);
        }
    }
}
