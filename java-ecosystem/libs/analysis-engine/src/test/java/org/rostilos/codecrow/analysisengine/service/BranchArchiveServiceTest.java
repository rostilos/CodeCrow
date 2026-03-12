package org.rostilos.codecrow.analysisengine.service;

import org.junit.jupiter.api.BeforeEach;
import org.junit.jupiter.api.Nested;
import org.junit.jupiter.api.Test;
import org.junit.jupiter.api.extension.ExtendWith;
import org.junit.jupiter.api.io.TempDir;
import org.mockito.Mock;
import org.mockito.junit.jupiter.MockitoExtension;
import org.rostilos.codecrow.core.model.vcs.VcsConnection;
import org.rostilos.codecrow.vcsclient.VcsClient;
import org.rostilos.codecrow.vcsclient.VcsClientProvider;

import java.io.ByteArrayOutputStream;
import java.io.IOException;
import java.nio.charset.StandardCharsets;
import java.nio.file.Files;
import java.nio.file.Path;
import java.util.Map;
import java.util.Set;
import java.util.zip.ZipEntry;
import java.util.zip.ZipOutputStream;

import static org.assertj.core.api.Assertions.assertThat;
import static org.assertj.core.api.Assertions.assertThatThrownBy;
import static org.mockito.ArgumentMatchers.*;
import static org.mockito.Mockito.*;

@ExtendWith(MockitoExtension.class)
class BranchArchiveServiceTest {

    @Mock private VcsClientProvider vcsClientProvider;
    @Mock private VcsClient vcsClient;

    private BranchArchiveService service;

    @BeforeEach
    void setUp() {
        service = new BranchArchiveService(vcsClientProvider);
    }

    // ── stripArchiveRoot ─────────────────────────────────────────────────

    @Nested
    class StripArchiveRoot {
        @Test
        void shouldStripFirstComponent() {
            assertThat(BranchArchiveService.stripArchiveRoot("repo-abc123/src/Foo.java"))
                    .isEqualTo("src/Foo.java");
        }

        @Test
        void noSlash_shouldReturnAsIs() {
            assertThat(BranchArchiveService.stripArchiveRoot("file.txt"))
                    .isEqualTo("file.txt");
        }

        @Test
        void trailingSlashOnly_shouldReturnAsIs() {
            assertThat(BranchArchiveService.stripArchiveRoot("root/"))
                    .isEqualTo("root/");
        }

        @Test
        void multipleSlashes_shouldStripOnlyFirst() {
            assertThat(BranchArchiveService.stripArchiveRoot("root/a/b/c.java"))
                    .isEqualTo("a/b/c.java");
        }
    }

    // ── downloadAndExtractFiles ──────────────────────────────────────────

    @Nested
    class DownloadAndExtract {

        @Test
        void shouldExtractRequestedFiles(@TempDir Path tempDir) throws Exception {
            VcsConnection conn = new VcsConnection();
            when(vcsClientProvider.getClient(conn)).thenReturn(vcsClient);

            // Create a real zip in-memory and have downloadRepositoryArchiveToFile write it
            byte[] zipBytes = createZip(Map.of(
                    "repo-abc/src/Foo.java", "public class Foo {}",
                    "repo-abc/src/Bar.java", "public class Bar {}",
                    "repo-abc/README.md", "# Readme"
            ));

            when(vcsClient.downloadRepositoryArchiveToFile(eq("ws"), eq("repo"), eq("main"), any(Path.class)))
                    .thenAnswer(inv -> {
                        Path target = inv.getArgument(3);
                        Files.write(target, zipBytes);
                        return (long) zipBytes.length;
                    });

            Map<String, String> result = service.downloadAndExtractFiles(
                    conn, "ws", "repo", "main", Set.of("src/Foo.java", "README.md"));

            assertThat(result).containsKey("src/Foo.java");
            assertThat(result).containsKey("README.md");
            assertThat(result.get("src/Foo.java")).isEqualTo("public class Foo {}");
            assertThat(result).doesNotContainKey("src/Bar.java"); // not in neededFiles
        }

        @Test
        void shouldSkipBinaryFiles(@TempDir Path tempDir) throws Exception {
            VcsConnection conn = new VcsConnection();
            when(vcsClientProvider.getClient(conn)).thenReturn(vcsClient);

            byte[] binaryContent = new byte[]{0, 1, 2, 3, 4, 5};
            ByteArrayOutputStream baos = new ByteArrayOutputStream();
            try (ZipOutputStream zos = new ZipOutputStream(baos)) {
                zos.putNextEntry(new ZipEntry("root/binary.dat"));
                zos.write(binaryContent);
                zos.closeEntry();
                zos.putNextEntry(new ZipEntry("root/text.txt"));
                zos.write("hello".getBytes(StandardCharsets.UTF_8));
                zos.closeEntry();
            }
            byte[] zipBytes = baos.toByteArray();

            when(vcsClient.downloadRepositoryArchiveToFile(anyString(), anyString(), anyString(), any(Path.class)))
                    .thenAnswer(inv -> {
                        Files.write(inv.getArgument(3, Path.class), zipBytes);
                        return (long) zipBytes.length;
                    });

            Map<String, String> result = service.downloadAndExtractFiles(
                    conn, "ws", "repo", "main", null);

            assertThat(result).containsKey("text.txt");
            assertThat(result).doesNotContainKey("binary.dat");
        }

        @Test
        void vcsClientThrows_shouldPropagate() throws Exception {
            VcsConnection conn = new VcsConnection();
            when(vcsClientProvider.getClient(conn)).thenReturn(vcsClient);
            when(vcsClient.downloadRepositoryArchiveToFile(anyString(), anyString(), anyString(), any(Path.class)))
                    .thenThrow(new IOException("Network error"));

            assertThatThrownBy(() -> service.downloadAndExtractFiles(
                    conn, "ws", "repo", "main", Set.of("a.java")))
                    .isInstanceOf(IOException.class)
                    .hasMessageContaining("Network error");
        }

        @Test
        void emptyZip_shouldReturnEmptyMap(@TempDir Path tempDir) throws Exception {
            VcsConnection conn = new VcsConnection();
            when(vcsClientProvider.getClient(conn)).thenReturn(vcsClient);

            ByteArrayOutputStream baos = new ByteArrayOutputStream();
            try (ZipOutputStream zos = new ZipOutputStream(baos)) {
                // empty zip
            }
            byte[] zipBytes = baos.toByteArray();

            when(vcsClient.downloadRepositoryArchiveToFile(anyString(), anyString(), anyString(), any(Path.class)))
                    .thenAnswer(inv -> {
                        Files.write(inv.getArgument(3, Path.class), zipBytes);
                        return (long) zipBytes.length;
                    });

            Map<String, String> result = service.downloadAndExtractFiles(
                    conn, "ws", "repo", "main", null);
            assertThat(result).isEmpty();
        }

        @Test
        void directoryEntries_shouldBeSkipped(@TempDir Path tempDir) throws Exception {
            VcsConnection conn = new VcsConnection();
            when(vcsClientProvider.getClient(conn)).thenReturn(vcsClient);

            ByteArrayOutputStream baos = new ByteArrayOutputStream();
            try (ZipOutputStream zos = new ZipOutputStream(baos)) {
                zos.putNextEntry(new ZipEntry("root/src/"));
                zos.closeEntry();
                zos.putNextEntry(new ZipEntry("root/src/Main.java"));
                zos.write("class Main {}".getBytes(StandardCharsets.UTF_8));
                zos.closeEntry();
            }
            byte[] zipBytes = baos.toByteArray();

            when(vcsClient.downloadRepositoryArchiveToFile(anyString(), anyString(), anyString(), any(Path.class)))
                    .thenAnswer(inv -> {
                        Files.write(inv.getArgument(3, Path.class), zipBytes);
                        return (long) zipBytes.length;
                    });

            Map<String, String> result = service.downloadAndExtractFiles(
                    conn, "ws", "repo", "main", null);
            assertThat(result).hasSize(1).containsKey("src/Main.java");
        }

        @Test
        void nullNeededFiles_shouldExtractAll(@TempDir Path tempDir) throws Exception {
            VcsConnection conn = new VcsConnection();
            when(vcsClientProvider.getClient(conn)).thenReturn(vcsClient);

            byte[] zipBytes = createZip(Map.of(
                    "root/a.java", "A",
                    "root/b.java", "B",
                    "root/c.java", "C"
            ));

            when(vcsClient.downloadRepositoryArchiveToFile(anyString(), anyString(), anyString(), any(Path.class)))
                    .thenAnswer(inv -> {
                        Files.write(inv.getArgument(3, Path.class), zipBytes);
                        return (long) zipBytes.length;
                    });

            Map<String, String> result = service.downloadAndExtractFiles(
                    conn, "ws", "repo", "main", null);
            assertThat(result).hasSize(3);
        }
    }

    // ── Helper ───────────────────────────────────────────────────────────

    private byte[] createZip(Map<String, String> entries) throws IOException {
        ByteArrayOutputStream baos = new ByteArrayOutputStream();
        try (ZipOutputStream zos = new ZipOutputStream(baos)) {
            for (var entry : entries.entrySet()) {
                zos.putNextEntry(new ZipEntry(entry.getKey()));
                zos.write(entry.getValue().getBytes(StandardCharsets.UTF_8));
                zos.closeEntry();
            }
        }
        return baos.toByteArray();
    }
}
