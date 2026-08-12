package org.rostilos.codecrow.core.persistence.repository.job;

import org.junit.jupiter.api.Test;
import org.springframework.data.domain.Pageable;
import org.springframework.data.jpa.repository.Query;

import java.lang.reflect.Method;
import java.time.OffsetDateTime;

import static org.assertj.core.api.Assertions.assertThat;

class LegacyRagJobRecoveryQueryTest {

    @Test
    void leaseRenewalIsGuardedByTypeStatusAgeAndMissingExactOperation()
            throws Exception {
        Query query = query(
                "renewLegacyRagJobLease",
                Long.class,
                OffsetDateTime.class,
                OffsetDateTime.class);

        assertThat(query.nativeQuery()).isTrue();
        assertThat(query.value())
                .contains("RAG_INCREMENTAL_INDEX")
                .contains("RUNNING")
                .contains("updated_at >= :validAfter")
                .contains("NOT EXISTS")
                .contains("rag_index_operation");
    }

    @Test
    void abandonedSelectionUsesScalarCoordinatesAndExcludesExactOperations()
            throws Exception {
        Query query = query(
                "findAbandonedLegacyRagJobs",
                OffsetDateTime.class,
                Pageable.class);

        assertThat(query.value())
                .contains("j.id AS jobId")
                .contains("j.project.id AS projectId")
                .contains("RAG_INCREMENTAL_INDEX")
                .contains("JobStatus.PENDING")
                .contains("JobStatus.RUNNING")
                .contains("j.updatedAt < :threshold")
                .contains("NOT EXISTS")
                .contains("RagIndexOperation");
    }

    @Test
    void abandonmentClaimRepeatsEveryLeaseFenceAtomically() throws Exception {
        Query query = query(
                "failAbandonedLegacyRagJob",
                Long.class,
                OffsetDateTime.class,
                OffsetDateTime.class,
                String.class);

        assertThat(query.nativeQuery()).isTrue();
        assertThat(query.value())
                .contains("status = 'FAILED'")
                .contains("RAG_INCREMENTAL_INDEX")
                .contains("'PENDING', 'QUEUED', 'RUNNING', 'WAITING'")
                .contains("updated_at < :threshold")
                .contains("NOT EXISTS")
                .contains("rag_index_operation");
    }

    @Test
    void completionRepeatsEveryLeaseFenceAtomically() throws Exception {
        Query query = query(
                "completeOwnedLegacyRagJob",
                Long.class,
                OffsetDateTime.class,
                OffsetDateTime.class);

        assertThat(query.nativeQuery()).isTrue();
        assertThat(query.value())
                .contains("status = 'COMPLETED'")
                .contains("RAG_INCREMENTAL_INDEX")
                .contains("status = 'RUNNING'")
                .contains("updated_at >= :validAfter")
                .contains("NOT EXISTS")
                .contains("rag_index_operation");
    }

    @Test
    void failedProjectionRepairIsOwnedByTheSameJob() throws Exception {
        Query query = query(
                "findFailedLegacyRagJobsWithActiveStatus",
                Pageable.class);

        assertThat(query.value())
                .contains("JobStatus.FAILED")
                .contains("s.activeJobId = j.id")
                .contains("RagIndexingStatus.INDEXING")
                .contains("RagIndexingStatus.UPDATING")
                .contains("NOT EXISTS")
                .contains("RagIndexOperation");
    }

    private static Query query(String name, Class<?>... parameterTypes)
            throws Exception {
        Method method = JobRepository.class.getDeclaredMethod(name, parameterTypes);
        Query query = method.getAnnotation(Query.class);
        assertThat(query).as("@Query on %s", name).isNotNull();
        return query;
    }
}
