package org.rostilos.codecrow.ragengine.characterization.p002;

import org.junit.jupiter.api.BeforeEach;
import org.junit.jupiter.api.Tag;
import org.junit.jupiter.api.Test;
import org.junit.jupiter.api.extension.ExtendWith;
import org.mockito.Mock;
import org.mockito.junit.jupiter.MockitoExtension;
import org.rostilos.codecrow.core.model.project.Project;
import org.rostilos.codecrow.core.model.vcs.VcsConnection;
import org.rostilos.codecrow.core.model.workspace.Workspace;
import org.rostilos.codecrow.ragengine.client.RagPipelineClient;
import org.rostilos.codecrow.ragengine.service.IncrementalRagUpdateService;
import org.rostilos.codecrow.ragengine.service.RagIndexTrackingService;
import org.rostilos.codecrow.vcsclient.VcsClient;
import org.rostilos.codecrow.vcsclient.VcsClientProvider;
import org.springframework.test.util.ReflectionTestUtils;

import java.io.IOException;
import java.util.LinkedHashSet;
import java.util.List;
import java.util.Map;
import java.util.Set;

import static org.assertj.core.api.Assertions.assertThat;
import static org.mockito.ArgumentMatchers.*;
import static org.mockito.Mockito.*;

@Tag("legacy-defect")
@ExtendWith(MockitoExtension.class)
class RagReadinessLegacyCharacterizationTest {

    @Mock private VcsClientProvider vcsClientProvider;
    @Mock private RagPipelineClient ragPipelineClient;
    @Mock private RagIndexTrackingService ragIndexTrackingService;

    private IncrementalRagUpdateService service;
    private Project project;

    @BeforeEach
    void setUp() {
        service = new IncrementalRagUpdateService(
                vcsClientProvider, ragPipelineClient, ragIndexTrackingService);
        ReflectionTestUtils.setField(service, "parallelRequests", 1);
        ReflectionTestUtils.setField(service, "ragApiRetryDelayMs", 0L);

        Workspace workspace = new Workspace();
        workspace.setName("offline-workspace");
        project = new Project();
        ReflectionTestUtils.setField(project, "id", 100L);
        project.setWorkspace(workspace);
        project.setName("offline-project");
        project.setNamespace("offline-project");
    }

    @Test
    void legacyDefectMissingOneOfTwoMandatoryFilesStillReportsCompleted() throws Exception {
        VcsClient vcsClient = mock(VcsClient.class);
        VcsConnection connection = new VcsConnection();
        doReturn(vcsClient).when(vcsClientProvider).getClient(any());
        doReturn("class Fetched {}").when(vcsClient)
                .getFileContent(anyString(), anyString(), eq("src/Fetched.java"), anyString());
        doReturn(null).when(vcsClient)
                .getFileContent(anyString(), anyString(), eq("src/Missing.java"), anyString());
        doReturn(Map.of("status", "ok")).when(ragPipelineClient)
                .updateFiles(anyList(), anyString(), anyString(), anyString(), anyString(), anyString());

        Map<String, Object> result = service.performIncrementalUpdate(
                project,
                connection,
                "offline-workspace",
                "offline-repository",
                "main",
                "head-a",
                new LinkedHashSet<>(List.of("src/Fetched.java", "src/Missing.java")),
                Set.of(),
                Set.of());

        assertThat(result)
                .containsEntry("status", "completed")
                .containsEntry("updatedFiles", 1)
                .containsEntry("addedFilesCount", 1);
        verify(ragPipelineClient).updateFiles(
                eq(List.of("src/Fetched.java")), anyString(), anyString(), anyString(), anyString(), anyString());
    }

    @Test
    void legacyDefectFetchIOExceptionAlsoReportsCompletedWithZeroUpdatedFiles() throws Exception {
        VcsClient vcsClient = mock(VcsClient.class);
        doReturn(vcsClient).when(vcsClientProvider).getClient(any());
        doThrow(new IOException("legacy injected timeout")).when(vcsClient)
                .getFileContent(anyString(), anyString(), anyString(), anyString());

        Map<String, Object> result = service.performIncrementalUpdate(
                project,
                new VcsConnection(),
                "offline-workspace",
                "offline-repository",
                "main",
                "head-a",
                Set.of("src/TimedOut.java"),
                Set.of(),
                Set.of());

        assertThat(result)
                .containsEntry("status", "completed")
                .containsEntry("updatedFiles", 0)
                .containsEntry("addedFilesCount", 0);
        verifyNoInteractions(ragPipelineClient);
    }
}
