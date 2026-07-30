package org.rostilos.codecrow.ragengine.service;

import org.junit.jupiter.api.BeforeEach;
import org.junit.jupiter.api.Test;
import org.junit.jupiter.api.extension.ExtendWith;
import org.mockito.Mock;
import org.mockito.junit.jupiter.MockitoExtension;
import org.rostilos.codecrow.analysisengine.service.BranchArchiveService;
import org.rostilos.codecrow.analysisengine.service.VcsFileRetrievalPolicy;
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
import java.util.LinkedHashSet;
import java.util.List;
import java.util.Map;
import java.util.Set;

import static org.assertj.core.api.Assertions.assertThat;
import static org.assertj.core.api.Assertions.assertThatThrownBy;
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

    @Mock
    private BranchArchiveService branchArchiveService;

    @Mock
    private VcsFileRetrievalPolicy fileRetrievalPolicy;

    private IncrementalRagUpdateService service;
    private Project testProject;

    @BeforeEach
    void setUp() {
        service = new IncrementalRagUpdateService(
                vcsClientProvider,
                ragPipelineClient,
                ragIndexTrackingService,
                branchArchiveService,
                fileRetrievalPolicy);
        ReflectionTestUtils.setField(service, "ragApiRetryDelayMs", 0L);
        lenient().when(fileRetrievalPolicy.archiveFileThreshold()).thenReturn(25);
        lenient().when(fileRetrievalPolicy.shouldUseArchive(anyInt())).thenReturn(false);

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

        assertThat(result.added()).isEmpty();
        assertThat(result.modified()).isEmpty();
        assertThat(result.deleted()).isEmpty();
    }

    @Test
    void testParseDiffForRag_NullDiff() {
        IncrementalRagUpdateService.DiffResult result = service.parseDiffForRag(null);

        assertThat(result.added()).isEmpty();
        assertThat(result.modified()).isEmpty();
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

        assertThat(result.added()).contains("src/NewFile.java");
        assertThat(result.modified()).isEmpty();
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
        assertThat(result.modified()).isEmpty();
        assertThat(result.added()).isEmpty();
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

        assertThat(result.modified()).contains("src/Modified.java");
        assertThat(result.added()).isEmpty();
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

        assertThat(result.added()).containsExactlyInAnyOrder("src/NewFile.java");
        assertThat(result.modified()).containsExactlyInAnyOrder("src/Modified.java");
        assertThat(result.deleted()).contains("src/OldFile.java");
    }

    @Test
    void testParseDiffForRag_CopyKeepsSourceAndAddsDestination() {
        String diff = "diff --git a/src/Original.java b/src/Copy.java\n" +
                "similarity index 100%\n" +
                "copy from src/Original.java\n" +
                "copy to src/Copy.java\n";

        IncrementalRagUpdateService.DiffResult result = service.parseDiffForRag(diff);

        assertThat(result.added()).containsExactly("src/Copy.java");
        assertThat(result.deleted()).isEmpty();
        assertThat(result.modified()).isEmpty();
    }

    @Test
    void testParseDiffForRag_RenameDeletesSourceAndAddsDestination() {
        String diff = "diff --git a/src/Old.java b/src/New.java\n" +
                "similarity index 100%\n" +
                "rename from src/Old.java\n" +
                "rename to src/New.java\n";

        IncrementalRagUpdateService.DiffResult result = service.parseDiffForRag(diff);

        assertThat(result.added()).containsExactly("src/New.java");
        assertThat(result.deleted()).containsExactly("src/Old.java");
        assertThat(result.modified()).isEmpty();
    }

    @Test
    void testParseDiffForRag_BlankDiff() {
        IncrementalRagUpdateService.DiffResult result = service.parseDiffForRag("   \n  \n  ");

        assertThat(result.added()).isEmpty();
        assertThat(result.modified()).isEmpty();
        assertThat(result.deleted()).isEmpty();
    }

    // ── performIncrementalUpdate ─────────────────────────────────────────────

    @Test
    void testPerformIncrementalUpdate_DeletesOnly() throws Exception {
        setupProjectWithWorkspace();
        VcsConnection vcsConn = new VcsConnection();

        when(ragPipelineClient.applyChanges(
                eq(List.of()), eq(List.of("deleted.java")), isNull(),
                eq("test-ws"), eq("test-proj"), eq("main"), eq("abc123")))
                .thenReturn(Map.of("status", "success"));

        Map<String, Object> result = service.performIncrementalUpdate(
                testProject, vcsConn, "ws-slug", "repo-slug", "main", "abc123",
                java.util.Collections.<String>emptySet(), java.util.Collections.<String>emptySet(),
                Set.of("deleted.java"));

        assertThat(result).containsEntry("status", "completed");
        assertThat(result).containsEntry("deletedFiles", 1);
        verify(ragPipelineClient).applyChanges(
                eq(List.of()), eq(List.of("deleted.java")), isNull(),
                eq("test-ws"), eq("test-proj"), eq("main"), eq("abc123"));
    }

    @Test
    void testPerformIncrementalUpdate_DeleteFails() throws Exception {
        setupProjectWithWorkspace();
        VcsConnection vcsConn = new VcsConnection();

        when(ragPipelineClient.applyChanges(
                anyList(), anyList(), nullable(String.class), anyString(),
                anyString(), anyString(), anyString()))
                .thenThrow(new IOException("Delete failed"));

        assertThatThrownBy(() -> service.performIncrementalUpdate(
                testProject, vcsConn, "ws-slug", "repo-slug", "main", "abc123",
                java.util.Collections.<String>emptySet(), java.util.Collections.<String>emptySet(),
                Set.of("deleted.java")))
                .isInstanceOf(IOException.class)
                .hasMessage("Delete failed");
    }

    @Test
    void testPerformIncrementalUpdate_UpdatesOnly() throws Exception {
        setupProjectWithWorkspace();
        ReflectionTestUtils.setField(service, "parallelRequests", 1);
        VcsConnection vcsConn = new VcsConnection();
        VcsClient mockVcsClient = mock(VcsClient.class);
        doReturn(mockVcsClient).when(vcsClientProvider).getClient(any());
        doReturn("public class Main {}").when(mockVcsClient).getFileContent(anyString(), anyString(), anyString(),
                anyString());
        doReturn(Map.of("status", "success")).when(ragPipelineClient).applyChanges(
                anyList(), anyList(), anyString(), anyString(), anyString(), anyString(), anyString());

        Map<String, Object> result = service.performIncrementalUpdate(
                testProject, vcsConn, "ws-slug", "repo-slug", "main", "abc123", Set.of("src/Main.java"),
                java.util.Collections.<String>emptySet(), java.util.Collections.<String>emptySet());

        assertThat(result).containsEntry("status", "completed");
        assertThat(result).containsKey("updatedFiles");
        assertThat(result).containsEntry("fileFetchMode", "per-file");
        verifyNoInteractions(branchArchiveService);
        verify(mockVcsClient).getFileContent("ws-slug", "repo-slug", "src/Main.java", "abc123");
        verify(ragPipelineClient).applyChanges(
                eq(List.of("src/Main.java")), eq(List.of()), anyString(),
                eq("test-ws"), eq("test-proj"), eq("main"), eq("abc123"));
    }

    @Test
    void testPerformIncrementalUpdate_AboveThresholdUsesSingleArchiveAtCommit() throws Exception {
        setupProjectWithWorkspace();
        when(fileRetrievalPolicy.shouldUseArchive(3)).thenReturn(true);
        VcsConnection vcsConn = new VcsConnection();
        Set<String> changedFiles = new LinkedHashSet<>(List.of(
                "src/A.java", "src/B.java", "src/C.java"));
        doReturn(archiveSnapshot(changedFiles, changedFiles))
                .when(branchArchiveService).downloadAndExtractSnapshotToDirectory(
                        eq(vcsConn), eq("ws-slug"), eq("repo-slug"), eq("abc123"),
                        eq(changedFiles), any());
        doReturn(Map.of("status", "success")).when(ragPipelineClient).applyChanges(
                anyList(), anyList(), anyString(), anyString(), anyString(), anyString(), anyString());

        Map<String, Object> result = service.performIncrementalUpdate(
                testProject, vcsConn, "ws-slug", "repo-slug", "main", "abc123",
                changedFiles, Set.of(), Set.of());

        assertThat(result).containsEntry("status", "completed");
        assertThat(result).containsEntry("updatedFiles", 3);
        assertThat(result).containsEntry("fileFetchMode", "archive");
        verify(branchArchiveService, times(1)).downloadAndExtractSnapshotToDirectory(
                eq(vcsConn), eq("ws-slug"), eq("repo-slug"), eq("abc123"), eq(changedFiles), any());
        verifyNoInteractions(vcsClientProvider);
        verify(ragPipelineClient).applyChanges(
                eq(List.of("src/A.java", "src/B.java", "src/C.java")),
                eq(List.of()), anyString(), eq("test-ws"), eq("test-proj"),
                eq("main"), eq("abc123"));
    }

    @Test
    void testPerformIncrementalUpdate_ArchiveFailureDoesNotFallBackToPerFileCalls() throws Exception {
        setupProjectWithWorkspace();
        when(fileRetrievalPolicy.shouldUseArchive(2)).thenReturn(true);
        VcsConnection vcsConn = new VcsConnection();
        Set<String> changedFiles = new LinkedHashSet<>(List.of("src/A.java", "src/B.java"));
        doThrow(new IOException("Archive rate limited")).when(branchArchiveService)
                .downloadAndExtractSnapshotToDirectory(
                        eq(vcsConn), eq("ws-slug"), eq("repo-slug"), eq("abc123"), eq(changedFiles), any());

        assertThatThrownBy(() -> service.performIncrementalUpdate(
                testProject, vcsConn, "ws-slug", "repo-slug", "main", "abc123",
                changedFiles, Set.of(), Set.of()))
                .isInstanceOf(IOException.class)
                .hasMessage("Archive rate limited");

        verifyNoInteractions(vcsClientProvider);
        verify(ragPipelineClient, never()).applyChanges(
                anyList(), anyList(), nullable(String.class), anyString(),
                anyString(), anyString(), anyString());
    }

    @Test
    void testPerformIncrementalUpdate_RateLimitSwitchesRemainingBatchToArchive() throws Exception {
        setupProjectWithWorkspace();
        ReflectionTestUtils.setField(service, "parallelRequests", 1);
        VcsConnection vcsConn = new VcsConnection();
        VcsClient mockVcsClient = mock(VcsClient.class);
        doReturn(mockVcsClient).when(vcsClientProvider).getClient(any());
        when(fileRetrievalPolicy.isRateLimited(any(IOException.class))).thenReturn(true);
        doThrow(new IOException("Unexpected response 429"))
                .when(mockVcsClient)
                .getFileContent(anyString(), anyString(), eq("src/A.java"), anyString());

        Set<String> changedFiles = new LinkedHashSet<>(List.of("src/A.java", "src/B.java"));
        doReturn(archiveSnapshot(changedFiles, changedFiles))
                .when(branchArchiveService).downloadAndExtractSnapshotToDirectory(
                        eq(vcsConn), eq("ws-slug"), eq("repo-slug"), eq("abc123"),
                        eq(changedFiles), any());
        doReturn(Map.of("status", "success")).when(ragPipelineClient).applyChanges(
                anyList(), anyList(), anyString(), anyString(), anyString(), anyString(), anyString());

        Map<String, Object> result = service.performIncrementalUpdate(
                testProject, vcsConn, "ws-slug", "repo-slug", "main", "abc123",
                changedFiles, Set.of(), Set.of());

        assertThat(result).containsEntry("status", "completed");
        assertThat(result).containsEntry("fileFetchMode", "archive-after-rate-limit");
        verify(mockVcsClient, times(1)).getFileContent(
                "ws-slug", "repo-slug", "src/A.java", "abc123");
        verify(mockVcsClient, never()).getFileContent(
                "ws-slug", "repo-slug", "src/B.java", "abc123");
        verify(branchArchiveService).downloadAndExtractSnapshotToDirectory(
                eq(vcsConn), eq("ws-slug"), eq("repo-slug"), eq("abc123"),
                eq(changedFiles), any());
    }

    @Test
    void testPerformIncrementalUpdate_SkipsMagentoImageAssetsWithoutAbortingArchiveBatch()
            throws Exception {
        setupProjectWithWorkspace();
        VcsConnection vcsConn = new VcsConnection();
        String sourceFile = "app/code/Andra/Returns/etc/di.xml";
        Set<String> imageFiles = Set.of(
                "app/design/frontend/AndraGroup/her/web/images/icons/Box.jpg",
                "app/design/frontend/AndraGroup/her/web/images/icons/Label.jpg",
                "app/design/frontend/AndraGroup/her/web/images/icons/Truck.jpg",
                "app/design/frontend/AndraGroup/her/web/images/rma-email/return-box.jpg",
                "app/design/frontend/AndraGroup/her/web/images/rma-email/return-label.jpg",
                "app/design/frontend/AndraGroup/her/web/images/rma-email/return-truck.jpg");
        Set<String> changedFiles = new LinkedHashSet<>();
        changedFiles.add(sourceFile);
        changedFiles.addAll(imageFiles);

        when(fileRetrievalPolicy.shouldUseArchive(1)).thenReturn(true);
        Set<String> requestedTextFiles = Set.of(sourceFile);
        doReturn(archiveSnapshot(requestedTextFiles, requestedTextFiles))
                .when(branchArchiveService).downloadAndExtractSnapshotToDirectory(
                        eq(vcsConn), eq("ws-slug"), eq("repo-slug"), eq("abc123"),
                        eq(requestedTextFiles), any());
        doReturn(Map.of("status", "success")).when(ragPipelineClient).applyChanges(
                anyList(), anyList(), anyString(), anyString(), anyString(), anyString(), anyString());

        Map<String, Object> result = service.performIncrementalUpdate(
                testProject, vcsConn, "ws-slug", "repo-slug", "main", "abc123",
                changedFiles, Set.of(), Set.of());

        assertThat(result)
                .containsEntry("status", "completed")
                .containsEntry("updatedFiles", 1)
                .containsEntry("skippedFiles", 6);
        verify(ragPipelineClient).applyChanges(
                eq(List.of(sourceFile)), eq(List.of()), anyString(),
                eq("test-ws"), eq("test-proj"), eq("main"), eq("abc123"));
    }

    @Test
    void testPerformIncrementalUpdate_SkipsPresentArchiveEntryThatIsNotText()
            throws Exception {
        setupProjectWithWorkspace();
        VcsConnection vcsConn = new VcsConnection();
        Set<String> changedFiles =
                new LinkedHashSet<>(List.of("src/Main.java", "assets/opaque.asset"));
        when(fileRetrievalPolicy.shouldUseArchive(2)).thenReturn(true);
        doReturn(archiveSnapshot(Set.of("src/Main.java"), changedFiles))
                .when(branchArchiveService).downloadAndExtractSnapshotToDirectory(
                        eq(vcsConn), eq("ws-slug"), eq("repo-slug"), eq("abc123"),
                        eq(changedFiles), any());
        doReturn(Map.of("status", "success")).when(ragPipelineClient).applyChanges(
                anyList(), anyList(), anyString(), anyString(), anyString(), anyString(), anyString());

        Map<String, Object> result = service.performIncrementalUpdate(
                testProject, vcsConn, "ws-slug", "repo-slug", "main", "abc123",
                changedFiles, Set.of(), Set.of());

        assertThat(result)
                .containsEntry("updatedFiles", 1)
                .containsEntry("skippedFiles", 1);
        verify(ragPipelineClient).applyChanges(
                eq(List.of("src/Main.java")), eq(List.of()), anyString(),
                anyString(), anyString(), anyString(), anyString());
    }

    @Test
    void testPerformIncrementalUpdate_SkipsUnknownBinaryInPerFileMode()
            throws Exception {
        setupProjectWithWorkspace();
        ReflectionTestUtils.setField(service, "parallelRequests", 1);
        VcsConnection vcsConn = new VcsConnection();
        VcsClient mockVcsClient = mock(VcsClient.class);
        doReturn(mockVcsClient).when(vcsClientProvider).getClient(any());
        doReturn("public class Main {}").when(mockVcsClient)
                .getFileContent("ws-slug", "repo-slug", "src/Main.java", "abc123");
        doReturn("binary\0payload").when(mockVcsClient)
                .getFileContent("ws-slug", "repo-slug", "assets/opaque.asset", "abc123");
        doReturn(Map.of("status", "success")).when(ragPipelineClient).applyChanges(
                anyList(), anyList(), anyString(), anyString(), anyString(), anyString(), anyString());

        Set<String> changedFiles =
                new LinkedHashSet<>(List.of("src/Main.java", "assets/opaque.asset"));
        Map<String, Object> result = service.performIncrementalUpdate(
                testProject, vcsConn, "ws-slug", "repo-slug", "main", "abc123",
                changedFiles, Set.of(), Set.of());

        assertThat(result)
                .containsEntry("updatedFiles", 1)
                .containsEntry("skippedFiles", 1)
                .containsEntry("fileFetchMode", "per-file");
        verify(ragPipelineClient).applyChanges(
                eq(List.of("src/Main.java")), eq(List.of()), anyString(),
                anyString(), anyString(), anyString(), anyString());
        verifyNoInteractions(branchArchiveService);
    }

    @Test
    void testPerformIncrementalUpdate_RetriesTimeoutThenSucceeds() throws Exception {
        setupProjectWithWorkspace();
        ReflectionTestUtils.setField(service, "parallelRequests", 1);
        ReflectionTestUtils.setField(service, "ragApiMaxAttempts", 3);
        VcsConnection vcsConn = new VcsConnection();
        VcsClient mockVcsClient = mock(VcsClient.class);
        doReturn(mockVcsClient).when(vcsClientProvider).getClient(any());
        doReturn("public class Main {}").when(mockVcsClient).getFileContent(anyString(), anyString(), anyString(),
                anyString());
        doThrow(new IOException("RAG API error: 500 - {\"detail\":\"timed out\"}"))
                .doReturn(Map.of("status", "success", "chunk_count", 5))
                .when(ragPipelineClient).applyChanges(
                        anyList(), anyList(), anyString(), anyString(),
                        anyString(), anyString(), anyString());

        Map<String, Object> result = service.performIncrementalUpdate(
                testProject, vcsConn, "ws-slug", "repo-slug", "main", "abc123", Set.of("src/Main.java"),
                java.util.Collections.<String>emptySet(), java.util.Collections.<String>emptySet());

        assertThat(result).containsEntry("status", "completed");
        assertThat(result).containsEntry("chunk_count", 5);
        verify(ragPipelineClient, times(2)).applyChanges(
                anyList(), anyList(), anyString(), anyString(),
                anyString(), anyString(), anyString());
    }

    @Test
    void testPerformIncrementalUpdate_SubmitsAllFilesInOneChangeSet() throws Exception {
        setupProjectWithWorkspace();
        ReflectionTestUtils.setField(service, "parallelRequests", 1);
        VcsConnection vcsConn = new VcsConnection();
        VcsClient mockVcsClient = mock(VcsClient.class);
        doReturn(mockVcsClient).when(vcsClientProvider).getClient(any());
        doReturn("content").when(mockVcsClient).getFileContent(anyString(), anyString(), anyString(), anyString());
        doReturn(Map.of("status", "ok")).when(ragPipelineClient).applyChanges(
                anyList(), anyList(), anyString(), anyString(), anyString(), anyString(), anyString());

        Map<String, Object> result = service.performIncrementalUpdate(
                testProject, vcsConn, "ws-slug", "repo-slug", "main", "abc123",
                new LinkedHashSet<>(List.of("src/B.java", "src/A.java")),
                java.util.Collections.<String>emptySet(), java.util.Collections.<String>emptySet());

        assertThat(result).containsEntry("status", "completed");
        assertThat(result).containsEntry("updatedFiles", 2);
        verify(ragPipelineClient).applyChanges(
                eq(List.of("src/A.java", "src/B.java")), eq(List.of()),
                anyString(), anyString(), anyString(), anyString(), anyString());
    }

    @Test
    void testPerformIncrementalUpdate_MissingFileAbortsBeforeMutation() throws Exception {
        setupProjectWithWorkspace();
        ReflectionTestUtils.setField(service, "parallelRequests", 1);
        VcsConnection vcsConn = new VcsConnection();
        VcsClient mockVcsClient = mock(VcsClient.class);
        doReturn(mockVcsClient).when(vcsClientProvider).getClient(any());
        doReturn("content").when(mockVcsClient).getFileContent(anyString(), anyString(), eq("src/Fetched.java"),
                anyString());
        doReturn(null).when(mockVcsClient).getFileContent(anyString(), anyString(), eq("src/Missing.java"),
                anyString());
        assertThatThrownBy(() -> service.performIncrementalUpdate(
                testProject, vcsConn, "ws-slug", "repo-slug", "main", "abc123",
                new LinkedHashSet<>(List.of("src/Fetched.java", "src/Missing.java")),
                java.util.Collections.<String>emptySet(), java.util.Collections.<String>emptySet()))
                .isInstanceOf(IOException.class)
                .hasMessageContaining("src/Missing.java");

        verifyNoInteractions(ragPipelineClient);
    }

    @Test
    void testPerformIncrementalUpdate_FetchFailureAbortsBeforeMutation() throws Exception {
        setupProjectWithWorkspace();
        ReflectionTestUtils.setField(service, "parallelRequests", 1);
        VcsConnection vcsConn = new VcsConnection();
        VcsClient mockVcsClient = mock(VcsClient.class);
        doReturn(mockVcsClient).when(vcsClientProvider).getClient(any());
        doThrow(new IOException("Network error")).when(mockVcsClient).getFileContent(anyString(), anyString(),
                anyString(), anyString());

        assertThatThrownBy(() -> service.performIncrementalUpdate(
                testProject, vcsConn, "ws-slug", "repo-slug", "main", "abc123", Set.of("src/Main.java"),
                java.util.Collections.<String>emptySet(), java.util.Collections.<String>emptySet()))
                .isInstanceOf(IOException.class)
                .hasMessageContaining("src/Main.java");
        verifyNoInteractions(ragPipelineClient);
    }

    @Test
    void testPerformIncrementalUpdate_NoChanges() throws Exception {
        setupProjectWithWorkspace();
        VcsConnection vcsConn = new VcsConnection();

        Map<String, Object> result = service.performIncrementalUpdate(
                testProject, vcsConn, "ws-slug", "repo-slug", "main", "abc123",
                java.util.Collections.<String>emptySet(), java.util.Collections.<String>emptySet(),
                java.util.Collections.<String>emptySet());

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
        doReturn(Map.of("status", "ok")).when(ragPipelineClient).applyChanges(
                anyList(), anyList(), anyString(), anyString(), anyString(), anyString(), anyString());

        Map<String, Object> result = service.performIncrementalUpdate(
                testProject, vcsConn, "ws-slug", "repo-slug", "main", "abc123", Set.of("src/New.java"),
                java.util.Collections.<String>emptySet(), Set.of("src/Old.java"));

        assertThat(result).containsEntry("status", "completed");
        assertThat(result).containsEntry("deletedFiles", 1);
        assertThat(result).containsKey("updatedFiles");
        verify(ragPipelineClient).applyChanges(
                eq(List.of("src/New.java")), eq(List.of("src/Old.java")),
                anyString(), eq("test-ws"), eq("test-proj"), eq("main"), eq("abc123"));
    }

    // ── Helpers ──────────────────────────────────────────────────────────────

    private void setupProjectWithWorkspace() {
        Workspace ws = new Workspace();
        ws.setName("test-ws");
        testProject.setWorkspace(ws);
        testProject.setName("test-proj");
        testProject.setNamespace("test-proj");
    }

    private BranchArchiveService.ArchiveDirectorySnapshot archiveSnapshot(
            Set<String> extractedFiles,
            Set<String> presentFiles) {
        return new BranchArchiveService.ArchiveDirectorySnapshot(
                extractedFiles, presentFiles);
    }
}
