package org.rostilos.codecrow.core.persistence.repository.job;

import org.junit.jupiter.api.Test;
import org.springframework.data.domain.Pageable;
import org.springframework.data.jpa.repository.Query;

import java.lang.reflect.Method;
import java.time.OffsetDateTime;

import static org.assertj.core.api.Assertions.assertThat;

class JobRepositoryRecoveryQueryTest {

    @Test
    void genericWebhookSelectionIsLimitedToPrAndBranchAnalysis() throws Exception {
        assertGenericWebhookTypes(query(
                "findRecoverableWebhookJobs",
                OffsetDateTime.class,
                Pageable.class));
        assertGenericWebhookTypes(query(
                "findAbandonedRunningWebhookJobs",
                OffsetDateTime.class,
                Pageable.class));
    }

    @Test
    void genericWebhookAtomicClaimsRepeatTheSameTypeBoundary() throws Exception {
        assertGenericWebhookTypes(query(
                "claimRecoverableWebhookJob",
                Long.class,
                OffsetDateTime.class,
                OffsetDateTime.class));
        assertGenericWebhookTypes(query(
                "claimAbandonedRunningWebhookJob",
                Long.class,
                OffsetDateTime.class,
                OffsetDateTime.class));
    }

    private static String query(String methodName, Class<?>... parameters)
            throws Exception {
        Method method = JobRepository.class.getDeclaredMethod(methodName, parameters);
        Query annotation = method.getAnnotation(Query.class);
        assertThat(annotation).as("@Query on %s", methodName).isNotNull();
        return annotation.value();
    }

    private static void assertGenericWebhookTypes(String query) {
        assertThat(query)
                .contains("JobTriggerSource.WEBHOOK")
                .contains("JobType.PR_ANALYSIS")
                .contains("JobType.BRANCH_ANALYSIS")
                .doesNotContain("JobType.BRANCH_RECONCILIATION")
                .doesNotContain("JobType.RAG_INITIAL_INDEX")
                .doesNotContain("JobType.RAG_INCREMENTAL_INDEX")
                .doesNotContain("JobType.MANUAL_ANALYSIS")
                .doesNotContain("JobType.REPO_SYNC")
                .doesNotContain("JobType.SUMMARIZE_COMMAND")
                .doesNotContain("JobType.ASK_COMMAND")
                .doesNotContain("JobType.ANALYZE_COMMAND")
                .doesNotContain("JobType.REVIEW_COMMAND")
                .doesNotContain("JobType.QA_DOC_COMMAND")
                .doesNotContain("JobType.IGNORED_COMMENT");
    }
}
