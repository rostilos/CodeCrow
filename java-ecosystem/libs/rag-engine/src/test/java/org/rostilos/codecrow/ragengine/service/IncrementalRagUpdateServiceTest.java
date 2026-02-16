package org.rostilos.codecrow.ragengine.service;

import org.junit.jupiter.api.BeforeEach;
import org.junit.jupiter.api.Test;
import org.junit.jupiter.api.extension.ExtendWith;
import org.mockito.Mock;
import org.mockito.junit.jupiter.MockitoExtension;
import org.rostilos.codecrow.core.model.project.Project;
import org.rostilos.codecrow.core.model.workspace.Workspace;
import org.rostilos.codecrow.core.model.project.config.ProjectConfig;
import org.rostilos.codecrow.core.model.project.config.RagConfig;
import org.rostilos.codecrow.core.model.vcs.VcsConnection;
import org.rostilos.codecrow.ragengine.client.RagPipelineClient;
import org.rostilos.codecrow.vcsclient.VcsClient;
import org.rostilos.codecrow.vcsclient.VcsClientProvider;
import org.springframework.test.util.ReflectionTestUtils;

import java.io.IOException;
import java.util.List;
import java.util.Map;
import java.util.Set;

import static org.assertj.core.api.Assertions.assertThat;
import static org.mockito.ArgumentMatchers.*;
import static org.mockito.Mockito.*;
import static org.mockito.Mockito.doReturn;

@ExtendWith(MockitoExtension.class)
class IncrementalRagUpdateServiceTest {

    @Mock
    private VcsClientProvider vcsClientProvider;

    @Mock
    private RagPipelineClient ragPipelineClient;

    @Mock
    private RagIndexTrackingService ragIndexTrackingService;

    private IncrementalRagUpdateService service;
    private Project testProject;

    @BeforeEach
    void setUp() {
        service = new IncrementalRagUpdateService(
                vcsClientProvider,
                ragPipelineClient,
                ragIndexTrackingService
        );
        
        testProject = new Project();
        ReflectionTestUtils.setField(testProject, "id", 100L);
    }

    // ── shouldPerformIncrementalUpdate ────────────────────────────────────────

    @Test
    void testShouldPerformIncrementalUpdate_RagDisabled() {
        ReflectionTestUtils.setField(service, "ragApiEnabled", false);

        boolean result = service.shouldPerformIncrementalUpdate(testProject);

        assertThat(result).isFalse();
    }

    @Test
    void testShouldPerformIncrementalUpdate_NoConfig() {
        ReflectionTestUtils.setField(service, "ragApiEnabled", true);
        testProject.setConfiguration(null);

        boolean result = service.shouldPerformIncrementalUpdate(testProject);

        assertThat(result).isFalse();
    }

    @Test
    void testShouldPerformIncrementalUpdate_NoRagConfig() {
        ReflectionTestUtils.setField(service, "ragApiEnabled", true);
        ProjectConfig config = new ProjectConfig();
        testProject.setConfiguration(config);

        boolean result = service.shouldPerformIncrementalUpdate(testProject);

        assertThat(result).isFalse();
    }

    @Test
    void testShouldPerformIncrementalUpdate_RagConfigDisabled() {
        ReflectionTestUtils.setField(service, "ragApiEnabled", true);
        RagConfig ragConfig = new RagConfig(false);
        testProject.setConfiguration(new ProjectConfig(false, "main", null, ragConfig));

        boolean result = service.shouldPerformIncrementalUpdate(testProject);

        assertThat(result).isFalse();
    }

    @Test
    void testShouldPerformIncrementalUpdate_ProjectNotIndexed() {
        ReflectionTestUtils.setField(service, "ragApiEnabled", true);
        RagConfig ragConfig = new RagConfig(true, "main");
        testProject.setConfiguration(new ProjectConfig(false, "main", null, ragConfig));
        when(ragIndexTrackingService.isProjectIndexed(testProject)).thenReturn(false);

        boolean result = service.shouldPerformIncrementalUpdate(testProject);

        assertThat(result).isFalse();
    }

    @Test
    void testShouldPerformIncrementalUpdate_Success() {
        ReflectionTestUtils.setField(service, "ragApiEnabled", true);
        RagConfig ragConfig = new RagConfig(true, "main");
        testProject.setConfiguration(new ProjectConfig(false, "main", null, ragConfig));
        when(ragIndexTrackingService.isProjectIndexed(testProject)).thenReturn(true);

        boolean result = service.shouldPerformIncrementalUpdate(testProject);

        assertThat(result).isTrue();
    }

    // ── parseDiffForRag ──────────────────────────────────────────────────────

    @Test
    void testParseDiffForRag_EmptyDiff() {
        IncrementalRagUpdateService.DiffResult result = service.parseDiffForRag("");

        assertThat(result.addedOrModified()).isEmpty();
        assertThat(result.deleted()).isEmpty();
    }

    @Test
    void testParseDiffForRag_NullDiff() {
        IncrementalRagUpdateService.DiffResult result = service.parseDiffForRag(null);

        assertThat(result.addedOrModified()).isEmpty();
        assertThat(result.deleted()).isEmpty();
    }

    @Test
    void testParseDiffForRag_AddedFile() {
        String diff = "diff --git a/src/NewFile.java b/src/NewFile.java\n" +
                      "new file mode 100644\n" +
                      "--- /dev/null\n" +
                      "+++ b/src/NewFile.java\n" +
                      "@@ -0,0 +1,10 @@\n" +
                      "+public class NewFile {}\n";

        IncrementalRagUpdateService.DiffResult result = service.parseDiffForRag(diff);

        assertThat(result.addedOrModified()).contains("src/NewFile.java");
        assertThat(result.deleted()).isEmpty();
    }

    @Test
    void testParseDiffForRag_DeletedFile() {
        String diff = "diff --git a/src/OldFile.java b/src/OldFile.java\n" +
                      "deleted file mode 100644\n" +
                      "--- a/src/OldFile.java\n" +
                      "+++ /dev/null\n" +
                      "@@ -1,10 +0,0 @@\n" +
                      "-public class OldFile {}\n";

        IncrementalRagUpdateService.DiffResult result = service.parseDiffForRag(diff);

        assertThat(result.deleted()).contains("src/OldFile.java");
        assertThat(result.addedOrModified()).isEmpty();
    }

    @Test
    void testParseDiffForRag_ModifiedFile() {
        String diff = "diff --git a/src/Modified.java b/src/Modified.java\n" +
                      "--- a/src/Modified.java\n" +
                      "+++ b/src/Modified.java\n" +
                      "@@ -1,5 +1,6 @@\n" +
                      " public class Modified {\n" +
                      "+    // new comment\n" +
                      " }\n";

        IncrementalRagUpdateService.DiffResult result = service.parseDiffForRag(diff);

        assertThat(result.addedOrModified()).contains("src/Modified.java");
        assertThat(result.deleted()).isEmpty();
    }

    @Test
    void testParseDiffForRag_MixedChanges() {
        String diff = "diff --git a/src/NewFile.java b/src/NewFile.java\n" +
                      "new file mode 100644\n" +
                      "--- /dev/null\n" +
                      "+++ b/src/NewFile.java\n" +
                      "@@ -0,0 +1 @@\n" +
                      "+new\n" +
                      "diff --git a/src/OldFile.java b/src/OldFile.java\n" +
                      "deleted file mode 100644\n" +
                      "--- a/src/OldFile.java\n" +
                      "+++ /dev/null\n" +
                      "@@ -1 +0,0 @@\n" +
                      "-old\n" +
                      "diff --git a/src/Modified.java b/src/Modified.java\n" +
                      "--- a/src/Modified.java\n" +
                      "+++ b/src/Modified.java\n" +
                      "@@ -1 +1 @@\n" +
                      "-old\n" +
                      "+new\n";

        IncrementalRagUpdateService.DiffResult result = service.parseDiffForRag(diff);

        assertThat(result.addedOrModified()).containsExactlyInAnyOrder("src/NewFile.java", "src/Modified.java");
        assertThat(result.deleted()).contains("src/OldFile.java");
    }

    @Test
    void testParseDiffForRag_BlankDiff() {
        IncrementalRagUpdateService.DiffResult result = service.parseDiffForRag("   \n  \n  ");

        assertThat(result.addedOrModified()).isEmpty();
        assertThat(result.deleted()).isEmpty();
    }

    // ── performIncrementalUpdate ─────────────────────────────────────────────

    @Test
    void testPerformIncrementalUpdate_DeletesOnly() throws Exception {
        setupProjectWithWorkspace();
        VcsConnection vcsConn = new VcsConnection();

        when(ragPipelineClient.deleteFiles(anyList(), eq("test-ws"), eq("test-proj"), eq("main")))
                .thenReturn(Map.of("status", "success"));

        Map<String, Object> result = service.performIncrementalUpdate(
                testProject, vcsConn, "ws-slug", "repo-slug",
                "main", "abc123",
                Set.of(), Set.of("deleted.java"));

        assertThat(result).containsEntry("status", "completed");
        assertThat(result).containsEntry("deletedFiles", 1);
        verify(ragPipelineClient).deleteFiles(anyList(), anyString(), anyString(), anyString());
        verify(ragPipelineClient, never()).updateFiles(anyList(), anyString(), anyString(), anyString(), anyString(), anyString());
    }

    @Test
    void testPerformIncrementalUpdate_DeleteFails() throws Exception {
        setupProjectWithWorkspace();
        VcsConnection vcsConn = new VcsConnection();

        when(ragPipelineClient.deleteFiles(anyList(), anyString(), anyString(), anyString()))
                .thenThrow(new IOException("Delete failed"));

        Map<String, Object> result = service.performIncrementalUpdate(
                testProject, vcsConn, "ws-slug", "repo-slug",
                "main", "abc123",
                Set.of(), Set.of("deleted.java"));

        assertThat(result).containsEntry("status", "completed");
        assertThat(result).containsKey("deleteError");
    }

    @Test
    void testPerformIncrementalUpdate_UpdatesOnly() throws Exception {
        setupProjectWithWorkspace();
        ReflectionTestUtils.setField(service, "parallelRequests", 1);
        VcsConnection vcsConn = new VcsConnection();
        VcsClient mockVcsClient = mock(VcsClient.class);
        doReturn(mockVcsClient).when(vcsClientProvider).getClient(any());
        doReturn("public class Main {}").when(mockVcsClient).getFileContent(anyString(), anyString(), anyString(), anyString());
        doReturn(Map.of("status", "success")).when(ragPipelineClient).updateFiles(anyList(), anyString(), anyString(), anyString(), anyString(), anyString());

        Map<String, Object> result = service.performIncrementalUpdate(
                testProject, vcsConn, "ws-slug", "repo-slug",
                "main", "abc123",
                Set.of("src/Main.java"), Set.of());

        assertThat(result).containsEntry("status", "completed");
        assertThat(result).containsKey("updatedFiles");
    }

    @Test
    void testPerformIncrementalUpdate_UpdateFailsGracefully() throws Exception {
        setupProjectWithWorkspace();
        ReflectionTestUtils.setField(service, "parallelRequests", 1);
        VcsConnection vcsConn = new VcsConnection();
        VcsClient mockVcsClient = mock(VcsClient.class);
        doReturn(mockVcsClient).when(vcsClientProvider).getClient(any());
        doThrow(new IOException("Network error")).when(mockVcsClient).getFileContent(anyString(), anyString(), anyString(), anyString());
        doReturn(Map.of("status", "success")).when(ragPipelineClient).updateFiles(anyList(), anyString(), anyString(), anyString(), anyString(), anyString());

        Map<String, Object> result = service.performIncrementalUpdate(
                testProject, vcsConn, "ws-slug", "repo-slug",
                "main", "abc123",
                Set.of("src/Main.java"), Set.of());

        assertThat(result).containsEntry("status", "completed");
        assertThat(result).containsEntry("updatedFiles", 0);
    }

    @Test
    void testPerformIncrementalUpdate_NoChanges() throws Exception {
        setupProjectWithWorkspace();
        VcsConnection vcsConn = new VcsConnection();

        Map<String, Object> result = service.performIncrementalUpdate(
                testProject, vcsConn, "ws-slug", "repo-slug",
                "main", "abc123",
                Set.of(), Set.of());

        assertThat(result).containsEntry("status", "completed");
        assertThat(result).containsEntry("branch", "main");
        assertThat(result).containsEntry("commitHash", "abc123");
        verifyNoInteractions(ragPipelineClient);
    }

    @Test
    void testPerformIncrementalUpdate_BothDeletesAndUpdates() throws Exception {
        setupProjectWithWorkspace();
        ReflectionTestUtils.setField(service, "parallelRequests", 1);
        VcsConnection vcsConn = new VcsConnection();
        VcsClient mockVcsClient = mock(VcsClient.class);
        doReturn(mockVcsClient).when(vcsClientProvider).getClient(any());
        doReturn("new content").when(mockVcsClient).getFileContent(anyString(), anyString(), anyString(), anyString());
        doReturn(Map.of("status", "ok")).when(ragPipelineClient).deleteFiles(anyList(), anyString(), anyString(), anyString());
        doReturn(Map.of("status", "ok")).when(ragPipelineClient).updateFiles(anyList(), anyString(), anyString(), anyString(), anyString(), anyString());

        Map<String, Object> result = service.performIncrementalUpdate(
                testProject, vcsConn, "ws-slug", "repo-slug",
                "main", "abc123",
                Set.of("src/New.java"), Set.of("src/Old.java"));

        assertThat(result).containsEntry("status", "completed");
        assertThat(result).containsEntry("deletedFiles", 1);
        assertThat(result).containsKey("updatedFiles");
    }

    // ── Helpers ──────────────────────────────────────────────────────────────

    private void setupProjectWithWorkspace() {
        Workspace ws = new Workspace();
        ws.setName("test-ws");
        testProject.setWorkspace(ws);
        testProject.setName("test-proj");
        testProject.setNamespace("test-proj");
    }
}
