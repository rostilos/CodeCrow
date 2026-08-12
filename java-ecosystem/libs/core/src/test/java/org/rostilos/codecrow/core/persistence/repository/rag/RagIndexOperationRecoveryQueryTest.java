package org.rostilos.codecrow.core.persistence.repository.rag;

import org.junit.jupiter.api.Test;
import org.springframework.data.jpa.repository.Query;

import java.lang.reflect.Method;
import java.time.OffsetDateTime;
import java.util.List;

import static org.assertj.core.api.Assertions.assertThat;

class RagIndexOperationRecoveryQueryTest {

    @Test
    void abandonedSelectionReturnsOnlyDetachedSafeScalarCoordinates()
            throws Exception {
        Method method = RagIndexOperationRepository.class.getDeclaredMethod(
                "findRecoverableOperationProjections", List.class, OffsetDateTime.class);
        Query annotation = method.getAnnotation(Query.class);

        assertThat(annotation).isNotNull();
        assertThat(method.getGenericReturnType().getTypeName())
                .contains("RecoveryOperationProjection")
                .doesNotContain("RagIndexOperation>");
        assertThat(annotation.value())
                .contains("o.project.id AS projectId")
                .doesNotContain("SELECT o FROM");
    }

    @Test
    void failedProjectionRecoveryRequiresExactStatusAndLockOwnership()
            throws Exception {
        String query = query("findFailedOperationsWithActiveProjections");

        assertThat(query)
                .contains("o.project_id AS \"projectId\"")
                .contains("s.active_job_id = o.job_id")
                .contains("l.lock_key = o.analysis_lock_key")
                .doesNotContain("s.active_job_id IS NULL");
    }

    @Test
    void succeededProjectionRecoveryRequiresExactStatusAndLockOwnership()
            throws Exception {
        String query = query("findSucceededOperationsWithActiveProjections");

        assertThat(query)
                .contains("s.active_job_id = o.job_id")
                .contains("l.lock_key = o.analysis_lock_key")
                .doesNotContain("s.active_job_id IS NULL");
    }

    private static String query(String methodName) throws Exception {
        Method method = RagIndexOperationRepository.class.getDeclaredMethod(methodName);
        Query annotation = method.getAnnotation(Query.class);
        assertThat(annotation).as("@Query on %s", methodName).isNotNull();
        assertThat(annotation.nativeQuery()).isTrue();
        return annotation.value();
    }
}
