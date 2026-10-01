package org.rostilos.codecrow.analysisengine.service;

import org.junit.jupiter.api.Test;
import org.junit.jupiter.api.io.TempDir;
import org.rostilos.codecrow.core.model.vcs.VcsConnection;
import org.rostilos.codecrow.vcsclient.VcsClient;
import org.rostilos.codecrow.vcsclient.VcsClientProvider;

import java.io.IOException;
import java.nio.file.Files;
import java.nio.file.Path;
import java.nio.file.attribute.FileTime;
import java.time.Instant;
import java.util.List;
import java.util.Map;

import static org.assertj.core.api.Assertions.assertThat;
import static org.mockito.ArgumentMatchers.any;
import static org.mockito.ArgumentMatchers.anyString;
import static org.mockito.ArgumentMatchers.isNull;
import static org.mockito.Mockito.doThrow;
import static org.mockito.Mockito.mock;
import static org.mockito.Mockito.verify;
import static org.mockito.Mockito.verifyNoInteractions;
import static org.mockito.Mockito.when;

class LocalRepositorySnapshotServiceTest {

    @Test
    void cleanupRemovesOnlyAbandonedReviewTrees() throws Exception {
        BranchArchiveService archiveService = mock(BranchArchiveService.class);
        VcsClientProvider vcsClientProvider = mock(VcsClientProvider.class);
        Path staleSnapshot = Files.createDirectory(
                temporaryRoot.resolve("codecrow-pr-review-stale"));
        Path staleOverlay = Files.createDirectory(
                temporaryRoot.resolve("codecrow-pr-overlay-stale"));
        Path activeSnapshot = Files.createDirectory(
                temporaryRoot.resolve("codecrow-pr-review-active"));
        Path unrelated = Files.createDirectory(
                temporaryRoot.resolve("another-service-stale"));
        FileTime stale = FileTime.from(Instant.now().minusSeconds(25 * 60 * 60));
        Files.setLastModifiedTime(staleSnapshot, stale);
        Files.setLastModifiedTime(staleOverlay, stale);
        Files.setLastModifiedTime(unrelated, stale);

        LocalRepositorySnapshotService service = new LocalRepositorySnapshotService(
                archiveService,
                vcsClientProvider,
                temporaryRoot);

        service.cleanupAbandonedSnapshots();

        assertThat(staleSnapshot).doesNotExist();
        assertThat(staleOverlay).doesNotExist();
        assertThat(activeSnapshot).exists();
        assertThat(unrelated).exists();
    }

    @TempDir
    Path temporaryRoot;

    @Test
    void usesSuppliedTargetHeadWithoutResolvingMovingBranch() throws Exception {
        BranchArchiveService archiveService = mock(BranchArchiveService.class);
        VcsClientProvider provider = mock(VcsClientProvider.class);
        VcsConnection connection = mock(VcsConnection.class);
        LocalRepositorySnapshotService service = new LocalRepositorySnapshotService(
                archiveService,
                provider,
                temporaryRoot);

        var prepared = service.prepare(
                connection,
                "team",
                "repo",
                "main",
                "pinned-target-head").orElseThrow();

        assertThat(prepared.revision()).isEqualTo("pinned-target-head");
        verifyNoInteractions(provider);
        verify(archiveService).downloadAndExtractSnapshotToDirectory(
                connection,
                "team",
                "repo",
                "pinned-target-head",
                null,
                prepared.path());
        prepared.close();
    }

    @Test
    void preparesPinnedTargetHeadAndDeletesWorkspaceOnClose() throws Exception {
        BranchArchiveService archiveService = mock(BranchArchiveService.class);
        VcsClientProvider provider = mock(VcsClientProvider.class);
        VcsClient client = mock(VcsClient.class);
        VcsConnection connection = mock(VcsConnection.class);
        when(provider.getClient(connection)).thenReturn(client);
        when(client.getLatestCommitHash("team", "repo", "main"))
                .thenReturn("target-head-sha");
        LocalRepositorySnapshotService service = new LocalRepositorySnapshotService(
                archiveService,
                provider,
                temporaryRoot);

        var prepared = service.prepare(connection, "team", "repo", "main").orElseThrow();

        assertThat(prepared.path()).exists().isDirectory();
        assertThat(prepared.targetBranch()).isEqualTo("main");
        assertThat(prepared.revision()).isEqualTo("target-head-sha");
        assertThat(prepared.transport().path()).isEqualTo(prepared.path().toString());
        verify(archiveService).downloadAndExtractSnapshotToDirectory(
                connection,
                "team",
                "repo",
                "target-head-sha",
                null,
                prepared.path());

        Path snapshotPath = prepared.path();
        prepared.close();
        assertThat(snapshotPath).doesNotExist();
    }

    @Test
    void preparesAndCleansRequestScopedProposedTreeOverlay() throws Exception {
        BranchArchiveService archiveService = mock(BranchArchiveService.class);
        VcsClientProvider provider = mock(VcsClientProvider.class);
        VcsConnection connection = mock(VcsConnection.class);
        LocalRepositorySnapshotService service = new LocalRepositorySnapshotService(
                archiveService,
                provider,
                temporaryRoot);

        var prepared = service.prepareForReview(
                connection,
                "team",
                "repo",
                "main",
                "target-head-sha",
                Map.of(
                        "src/Changed.java", "class Changed { int proposed; }\n",
                        "../outside.java", "must not be written"),
                List.of("src/Changed.java", "src/Unavailable.java", "../outside.java"),
                List.of("src/Deleted.java"))
                .orElseThrow();

        Path overlay = prepared.reviewOverlayPath();
        assertThat(overlay).exists().isDirectory();
        assertThat(overlay.resolve("files/src/Changed.java"))
                .hasContent("class Changed { int proposed; }\n");
        assertThat(overlay.resolve("files/outside.java")).doesNotExist();
        assertThat(Files.readString(overlay.resolve("manifest.json")))
                .contains("src/Changed.java", "src/Unavailable.java", "src/Deleted.java")
                .doesNotContain("outside.java");
        assertThat(prepared.transport().reviewOverlayPath()).isEqualTo(overlay.toString());

        Path snapshot = prepared.path();
        prepared.close();
        assertThat(snapshot).doesNotExist();
        assertThat(overlay).doesNotExist();
    }

    @Test
    void retainsExactProposedBodiesForReadsAcrossReviewBatches() throws Exception {
        BranchArchiveService archiveService = mock(BranchArchiveService.class);
        VcsClientProvider provider = mock(VcsClientProvider.class);
        VcsConnection connection = mock(VcsConnection.class);
        LocalRepositorySnapshotService service = new LocalRepositorySnapshotService(
                archiveService,
                provider,
                temporaryRoot);

        var prepared = service.prepareForReview(
                connection,
                "team",
                "repo",
                "main",
                "target-head-sha",
                Map.of(
                        "src/BatchOne.java", "class BatchOne { int proposed; }\n",
                        "src/BatchThreeDependency.java",
                        "class BatchThreeDependency { int proposed; }\n"),
                List.of(
                        "src/BatchOne.java",
                        "src/BatchThreeDependency.java",
                        "src/Unavailable.java"),
                List.of("src/Deleted.java"))
                .orElseThrow();

        Path overlay = prepared.reviewOverlayPath();
        assertThat(overlay.resolve("files/src/BatchOne.java"))
                .hasContent("class BatchOne { int proposed; }\n");
        assertThat(overlay.resolve("files/src/BatchThreeDependency.java"))
                .hasContent("class BatchThreeDependency { int proposed; }\n");
        assertThat(overlay.resolve("files/src/Unavailable.java")).doesNotExist();
        assertThat(overlay.resolve("files/src/Deleted.java")).doesNotExist();
        assertThat(Files.readString(overlay.resolve("manifest.json")))
                .contains(
                        "src/BatchOne.java",
                        "src/BatchThreeDependency.java",
                        "src/Unavailable.java",
                        "src/Deleted.java");

        prepared.close();
    }

    @Test
    void archiveFailureCleansPartialWorkspaceAndFallsBack() throws Exception {
        BranchArchiveService archiveService = mock(BranchArchiveService.class);
        VcsClientProvider provider = mock(VcsClientProvider.class);
        VcsClient client = mock(VcsClient.class);
        VcsConnection connection = mock(VcsConnection.class);
        when(provider.getClient(connection)).thenReturn(client);
        when(client.getLatestCommitHash("team", "repo", "main"))
                .thenReturn("target-head-sha");
        doThrow(new IOException("archive unavailable"))
                .when(archiveService)
                .downloadAndExtractSnapshotToDirectory(
                        any(VcsConnection.class),
                        anyString(),
                        anyString(),
                        anyString(),
                        isNull(),
                        any(Path.class));
        LocalRepositorySnapshotService service = new LocalRepositorySnapshotService(
                archiveService,
                provider,
                temporaryRoot);

        assertThat(service.prepare(connection, "team", "repo", "main")).isEmpty();
        try (var children = Files.list(temporaryRoot)) {
            assertThat(children).isEmpty();
        }
    }

    @Test
    void missingTargetBranchSkipsProviderAndArchive() {
        BranchArchiveService archiveService = mock(BranchArchiveService.class);
        VcsClientProvider provider = mock(VcsClientProvider.class);
        LocalRepositorySnapshotService service = new LocalRepositorySnapshotService(
                archiveService,
                provider,
                temporaryRoot);

        assertThat(service.prepare(mock(VcsConnection.class), "team", "repo", " ")).isEmpty();
        verifyNoInteractions(provider, archiveService);
    }
}
