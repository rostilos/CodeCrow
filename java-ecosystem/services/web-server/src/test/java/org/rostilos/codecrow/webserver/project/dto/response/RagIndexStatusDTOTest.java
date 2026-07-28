package org.rostilos.codecrow.webserver.project.dto.response;

import org.junit.jupiter.api.Test;
import org.rostilos.codecrow.core.model.analysis.RagIndexStatus;
import org.rostilos.codecrow.core.model.analysis.RagIndexingStatus;
import org.rostilos.codecrow.core.model.project.Project;
import org.springframework.test.util.ReflectionTestUtils;

import java.time.OffsetDateTime;

import static org.assertj.core.api.Assertions.assertThat;

class RagIndexStatusDTOTest {

    @Test
    void exposesCurrentIndexActivityTimestamp() {
        Project project = new Project();
        ReflectionTestUtils.setField(project, "id", 42L);

        OffsetDateTime activity = OffsetDateTime.now().minusSeconds(10);
        RagIndexStatus status = new RagIndexStatus();
        status.setProject(project);
        status.setStatus(RagIndexingStatus.INDEXING);
        status.setUpdatedAt(activity);

        RagIndexStatusDTO dto = RagIndexStatusDTO.fromEntity(status);

        assertThat(dto.projectId()).isEqualTo(42L);
        assertThat(dto.status()).isEqualTo(RagIndexingStatus.INDEXING);
        assertThat(dto.updatedAt()).isEqualTo(activity);
    }
}
