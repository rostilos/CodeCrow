package org.rostilos.codecrow.webserver.project.service;

import org.junit.jupiter.api.Test;
import org.rostilos.codecrow.core.model.project.Project;
import org.rostilos.codecrow.core.model.project.config.ProjectConfig;
import org.rostilos.codecrow.core.model.project.config.RagConfig;
import org.rostilos.codecrow.core.model.rag.RagBranchIndex;
import org.rostilos.codecrow.core.model.rag.RagBranchIndexKind;
import org.rostilos.codecrow.core.model.rag.RagBranchIndexLifecycleStatus;
import org.rostilos.codecrow.core.persistence.repository.rag.RagBranchIndexRepository;
import org.springframework.test.util.ReflectionTestUtils;

import java.time.OffsetDateTime;
import java.util.List;

import static org.assertj.core.api.Assertions.assertThat;
import static org.mockito.Mockito.mock;
import static org.mockito.Mockito.when;

class RagBranchIndexStatusServiceTest {

    @Test
    void reportsPrimaryAndEveryLazilyObservedBranch() {
        RagBranchIndexRepository branches = mock(RagBranchIndexRepository.class);
        RagBranchIndexStatusService service = new RagBranchIndexStatusService(branches);

        Project project = new Project();
        ReflectionTestUtils.setField(project, "id", 42L);
        ProjectConfig config = new ProjectConfig();
        config.setRagConfig(new RagConfig(true, "master"));
        project.setConfiguration(config);

        RagBranchIndex develop = new RagBranchIndex(project, "develop", RagBranchIndexKind.DURABLE);
        develop.setLifecycleStatus(RagBranchIndexLifecycleStatus.FAILED);
        develop.setErrorMessage("archive unavailable");
        develop.setUpdatedAt(OffsetDateTime.parse("2026-08-07T00:00:00Z"));

        RagBranchIndex temporary = new RagBranchIndex(project, "release/candidate", RagBranchIndexKind.TRANSIENT);
        temporary.setLifecycleStatus(RagBranchIndexLifecycleStatus.READY);

        when(branches.findByProjectId(42L)).thenReturn(List.of(develop, temporary));
        var statuses = service.getConfiguredBranches(project);

        assertThat(statuses).extracting(value -> value.branchName())
                .containsExactly("master", "develop", "release/candidate");
        assertThat(statuses.get(0).status()).isEqualTo("NOT_INDEXED");
        assertThat(statuses.get(1))
                .extracting(value -> value.role(), value -> value.status(), value -> value.errorMessage())
                .containsExactly("TARGET", "FAILED", "archive unavailable");
    }
}
