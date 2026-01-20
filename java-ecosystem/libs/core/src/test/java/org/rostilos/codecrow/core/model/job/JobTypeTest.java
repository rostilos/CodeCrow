package org.rostilos.codecrow.core.model.job;

import org.junit.jupiter.api.DisplayName;
import org.junit.jupiter.api.Test;

import static org.assertj.core.api.Assertions.assertThat;

@DisplayName("JobType")
class JobTypeTest {

    @Test
    @DisplayName("should have all expected values")
    void shouldHaveAllExpectedValues() {
        JobType[] values = JobType.values();
        
        assertThat(values).contains(
                JobType.PR_ANALYSIS,
                JobType.BRANCH_ANALYSIS,
                JobType.BRANCH_RECONCILIATION,
                JobType.RAG_INITIAL_INDEX,
                JobType.RAG_INCREMENTAL_INDEX,
                JobType.MANUAL_ANALYSIS,
                JobType.REPO_SYNC,
                JobType.SUMMARIZE_COMMAND,
                JobType.ASK_COMMAND,
                JobType.ANALYZE_COMMAND,
                JobType.REVIEW_COMMAND,
                JobType.IGNORED_COMMENT
        );
    }

    @Test
    @DisplayName("valueOf should return correct enum for analysis types")
    void valueOfShouldReturnCorrectEnumForAnalysisTypes() {
        assertThat(JobType.valueOf("PR_ANALYSIS")).isEqualTo(JobType.PR_ANALYSIS);
        assertThat(JobType.valueOf("BRANCH_ANALYSIS")).isEqualTo(JobType.BRANCH_ANALYSIS);
        assertThat(JobType.valueOf("MANUAL_ANALYSIS")).isEqualTo(JobType.MANUAL_ANALYSIS);
    }

    @Test
    @DisplayName("valueOf should return correct enum for command types")
    void valueOfShouldReturnCorrectEnumForCommandTypes() {
        assertThat(JobType.valueOf("SUMMARIZE_COMMAND")).isEqualTo(JobType.SUMMARIZE_COMMAND);
        assertThat(JobType.valueOf("ASK_COMMAND")).isEqualTo(JobType.ASK_COMMAND);
        assertThat(JobType.valueOf("ANALYZE_COMMAND")).isEqualTo(JobType.ANALYZE_COMMAND);
        assertThat(JobType.valueOf("REVIEW_COMMAND")).isEqualTo(JobType.REVIEW_COMMAND);
    }

    @Test
    @DisplayName("valueOf should return correct enum for RAG types")
    void valueOfShouldReturnCorrectEnumForRagTypes() {
        assertThat(JobType.valueOf("RAG_INITIAL_INDEX")).isEqualTo(JobType.RAG_INITIAL_INDEX);
        assertThat(JobType.valueOf("RAG_INCREMENTAL_INDEX")).isEqualTo(JobType.RAG_INCREMENTAL_INDEX);
    }

    @Test
    @DisplayName("name should return string representation")
    void nameShouldReturnStringRepresentation() {
        assertThat(JobType.PR_ANALYSIS.name()).isEqualTo("PR_ANALYSIS");
        assertThat(JobType.IGNORED_COMMENT.name()).isEqualTo("IGNORED_COMMENT");
    }
}
