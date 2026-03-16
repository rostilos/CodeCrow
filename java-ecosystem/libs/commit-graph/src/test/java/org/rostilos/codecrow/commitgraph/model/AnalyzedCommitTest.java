package org.rostilos.codecrow.commitgraph.model;

import org.junit.jupiter.api.Test;
import org.rostilos.codecrow.core.model.codeanalysis.AnalysisType;
import org.rostilos.codecrow.core.model.project.Project;

import java.time.OffsetDateTime;

import static org.assertj.core.api.Assertions.assertThat;

class AnalyzedCommitTest {

    @Test
    void noArgConstructor_shouldCreateEmptyInstance() {
        AnalyzedCommit commit = new AnalyzedCommit();

        assertThat(commit.getId()).isNull();
        assertThat(commit.getProject()).isNull();
        assertThat(commit.getCommitHash()).isNull();
        assertThat(commit.getAnalysisId()).isNull();
        assertThat(commit.getAnalysisType()).isNull();
        assertThat(commit.getAnalyzedAt()).isNotNull(); // default OffsetDateTime.now()
    }

    @Test
    void threeArgConstructor_shouldSetFieldsAndTimestamp() {
        Project project = new Project();
        AnalyzedCommit commit = new AnalyzedCommit(project, "abc123def", AnalysisType.BRANCH_ANALYSIS);

        assertThat(commit.getProject()).isSameAs(project);
        assertThat(commit.getCommitHash()).isEqualTo("abc123def");
        assertThat(commit.getAnalysisType()).isEqualTo(AnalysisType.BRANCH_ANALYSIS);
        assertThat(commit.getAnalysisId()).isNull();
        assertThat(commit.getAnalyzedAt()).isNotNull();
        assertThat(commit.getAnalyzedAt()).isBefore(OffsetDateTime.now().plusSeconds(1));
    }

    @Test
    void fourArgConstructor_shouldSetAnalysisId() {
        Project project = new Project();
        AnalyzedCommit commit = new AnalyzedCommit(project, "def456", 42L, AnalysisType.PR_REVIEW);

        assertThat(commit.getProject()).isSameAs(project);
        assertThat(commit.getCommitHash()).isEqualTo("def456");
        assertThat(commit.getAnalysisId()).isEqualTo(42L);
        assertThat(commit.getAnalysisType()).isEqualTo(AnalysisType.PR_REVIEW);
        assertThat(commit.getAnalyzedAt()).isNotNull();
    }

    @Test
    void settersAndGetters_shouldRoundTrip() {
        AnalyzedCommit commit = new AnalyzedCommit();

        commit.setId(1L);
        assertThat(commit.getId()).isEqualTo(1L);

        Project project = new Project();
        commit.setProject(project);
        assertThat(commit.getProject()).isSameAs(project);

        commit.setCommitHash("hash123");
        assertThat(commit.getCommitHash()).isEqualTo("hash123");

        OffsetDateTime now = OffsetDateTime.now();
        commit.setAnalyzedAt(now);
        assertThat(commit.getAnalyzedAt()).isEqualTo(now);

        commit.setAnalysisId(99L);
        assertThat(commit.getAnalysisId()).isEqualTo(99L);

        commit.setAnalysisType(AnalysisType.PR_REVIEW);
        assertThat(commit.getAnalysisType()).isEqualTo(AnalysisType.PR_REVIEW);
    }

    @Test
    void fourArgConstructor_withNullAnalysisId_shouldSetNull() {
        Project project = new Project();
        AnalyzedCommit commit = new AnalyzedCommit(project, "xyz", null, AnalysisType.BRANCH_ANALYSIS);

        assertThat(commit.getAnalysisId()).isNull();
        assertThat(commit.getAnalysisType()).isEqualTo(AnalysisType.BRANCH_ANALYSIS);
    }
}
