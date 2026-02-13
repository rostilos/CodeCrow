package org.rostilos.codecrow.ragengine.service;

import org.junit.jupiter.api.BeforeEach;
import org.junit.jupiter.api.DisplayName;
import org.junit.jupiter.api.Nested;
import org.junit.jupiter.api.Test;
import org.junit.jupiter.api.extension.ExtendWith;
import org.junit.jupiter.api.io.TempDir;
import org.mockito.Mock;
import org.mockito.junit.jupiter.MockitoExtension;
import org.rostilos.codecrow.ragengine.client.RagPipelineClient;

import java.io.*;
import java.nio.file.Files;
import java.nio.file.Path;
import java.util.List;
import java.util.Map;
import java.util.zip.ZipEntry;
import java.util.zip.ZipOutputStream;

import static org.assertj.core.api.Assertions.*;
import static org.mockito.ArgumentMatchers.*;
import static org.mockito.Mockito.*;

@ExtendWith(MockitoExtension.class)
@DisplayName("RagIndexingService")
class RagIndexingServiceTest {

    @Mock
    private RagPipelineClient ragClient;

    private RagIndexingService ragIndexingService;

    @BeforeEach
    void setUp() {
        ragIndexingService = new RagIndexingService(ragClient);
    }

    @Nested
    @DisplayName("isAvailable()")
    class IsAvailableTests {
        @Test
        @DisplayName("should return true when client is healthy")
        void shouldReturnTrueWhenHealthy() {
            when(ragClient.isHealthy()).thenReturn(true);
            assertThat(ragIndexingService.isAvailable()).isTrue();
        }

        @Test
        @DisplayName("should return false when client is not healthy")
        void shouldReturnFalseWhenNotHealthy() {
            when(ragClient.isHealthy()).thenReturn(false);
            assertThat(ragIndexingService.isAvailable()).isFalse();
        }
    }

    @Nested
    @DisplayName("indexRepository()")
    class IndexRepositoryTests {
        @Test
        @DisplayName("should delegate to ragClient without exclude patterns")
        void shouldDelegateWithoutPatterns() throws IOException {
            Map<String, Object> expected = Map.of("document_count", 100);
            when(ragClient.indexRepository(anyString(), anyString(), anyString(), anyString(), anyString(), isNull(), isNull()))
                    .thenReturn(expected);

            Map<String, Object> result = ragIndexingService.indexRepository(
                    "/tmp/repo", "ws", "proj", "main", "abc123");

            assertThat(result).containsEntry("document_count", 100);
            verify(ragClient).indexRepository("/tmp/repo", "ws", "proj", "main", "abc123", null, null);
        }

        @Test
        @DisplayName("should delegate to ragClient with exclude patterns")
        void shouldDelegateWithPatterns() throws IOException {
            List<String> patterns = List.of("*.log", "vendor/**");
            Map<String, Object> expected = Map.of("document_count", 80);
            when(ragClient.indexRepository(anyString(), anyString(), anyString(), anyString(), anyString(), isNull(), eq(patterns)))
                    .thenReturn(expected);

            Map<String, Object> result = ragIndexingService.indexRepository(
                    "/tmp/repo", "ws", "proj", "main", "abc123", null, patterns);

            assertThat(result).containsEntry("document_count", 80);
        }
    }

    @Nested
    @DisplayName("updateChangedFiles()")
    class UpdateChangedFilesTests {
        @Test
        @DisplayName("should delegate file updates to ragClient")
        void shouldDelegateFileUpdates() throws IOException {
            List<String> files = List.of("src/main.java", "src/test.java");
            Map<String, Object> expected = Map.of("updated", 2);
            when(ragClient.updateFiles(eq(files), anyString(), anyString(), anyString(), anyString(), anyString()))
                    .thenReturn(expected);

            Map<String, Object> result = ragIndexingService.updateChangedFiles(
                    files, "/repo", "ws", "proj", "main", "abc123");

            assertThat(result).containsEntry("updated", 2);
        }
    }

    @Nested
    @DisplayName("indexFromArchiveFile()")
    class IndexFromArchiveFileTests {

        @TempDir
        Path tempDir;

        @Test
        @DisplayName("should extract archive and index repository")
        void shouldExtractAndIndex() throws IOException {
            // Create a test zip file
            Path zipFile = tempDir.resolve("test.zip");
            createTestZip(zipFile, Map.of(
                    "src/Main.java", "public class Main {}",
                    "README.md", "# Test"
            ));

            Map<String, Object> expected = Map.of("document_count", 2);
            when(ragClient.indexRepository(anyString(), anyString(), anyString(), anyString(), anyString(), isNull(), isNull()))
                    .thenReturn(expected);

            Map<String, Object> result = ragIndexingService.indexFromArchiveFile(
                    zipFile, "ws", "proj", "main", "abc123", null, null);

            assertThat(result).containsEntry("document_count", 2);
            verify(ragClient).indexRepository(anyString(), eq("ws"), eq("proj"), eq("main"), eq("abc123"), isNull(), isNull());
        }

        @Test
        @DisplayName("should pass exclude patterns to client")
        void shouldPassExcludePatterns() throws IOException {
            Path zipFile = tempDir.resolve("test.zip");
            createTestZip(zipFile, Map.of("src/Main.java", "content"));

            List<String> patterns = List.of("*.log");
            when(ragClient.indexRepository(anyString(), anyString(), anyString(), anyString(), anyString(), isNull(), eq(patterns)))
                    .thenReturn(Map.of("document_count", 1));

            ragIndexingService.indexFromArchiveFile(zipFile, "ws", "proj", "main", "abc123", null, patterns);

            verify(ragClient).indexRepository(anyString(), eq("ws"), eq("proj"), eq("main"), eq("abc123"), isNull(), eq(patterns));
        }

        @Test
        @DisplayName("should handle zip with directories")
        void shouldHandleZipWithDirectories() throws IOException {
            Path zipFile = tempDir.resolve("test.zip");
            try (ZipOutputStream zos = new ZipOutputStream(new FileOutputStream(zipFile.toFile()))) {
                // Add directory entry
                zos.putNextEntry(new ZipEntry("src/"));
                zos.closeEntry();
                // Add file in directory
                zos.putNextEntry(new ZipEntry("src/Main.java"));
                zos.write("content".getBytes());
                zos.closeEntry();
            }

            when(ragClient.indexRepository(anyString(), anyString(), anyString(), anyString(), anyString(), isNull(), isNull()))
                    .thenReturn(Map.of("document_count", 1));

            Map<String, Object> result = ragIndexingService.indexFromArchiveFile(
                    zipFile, "ws", "proj", "main", "abc123", null, null);

            assertThat(result).containsEntry("document_count", 1);
        }
    }

    @Nested
    @DisplayName("indexFromArchive() [deprecated]")
    class IndexFromArchiveTests {

        @Test
        @DisplayName("should extract byte array archive and index")
        void shouldExtractAndIndex() throws IOException {
            byte[] zipData = createTestZipBytes(Map.of("src/Main.java", "content"));

            when(ragClient.indexRepository(anyString(), anyString(), anyString(), anyString(), anyString(), isNull(), isNull()))
                    .thenReturn(Map.of("document_count", 1));

            Map<String, Object> result = ragIndexingService.indexFromArchive(
                    zipData, "ws", "proj", "main", "abc123", null, null);

            assertThat(result).containsEntry("document_count", 1);
        }

        @Test
        @DisplayName("should pass exclude patterns")
        void shouldPassExcludePatterns() throws IOException {
            byte[] zipData = createTestZipBytes(Map.of("src/Main.java", "content"));
            List<String> patterns = List.of("*.test");

            when(ragClient.indexRepository(anyString(), anyString(), anyString(), anyString(), anyString(), isNull(), eq(patterns)))
                    .thenReturn(Map.of("document_count", 1));

            ragIndexingService.indexFromArchive(zipData, "ws", "proj", "main", "abc123", null, patterns);

            verify(ragClient).indexRepository(anyString(), eq("ws"), eq("proj"), eq("main"), eq("abc123"), isNull(), eq(patterns));
        }
    }

    // ── Helpers ──────────────────────────────────────────────────────────────

    private void createTestZip(Path zipPath, Map<String, String> files) throws IOException {
        try (ZipOutputStream zos = new ZipOutputStream(new FileOutputStream(zipPath.toFile()))) {
            for (Map.Entry<String, String> entry : files.entrySet()) {
                zos.putNextEntry(new ZipEntry(entry.getKey()));
                zos.write(entry.getValue().getBytes());
                zos.closeEntry();
            }
        }
    }

    private byte[] createTestZipBytes(Map<String, String> files) throws IOException {
        ByteArrayOutputStream baos = new ByteArrayOutputStream();
        try (ZipOutputStream zos = new ZipOutputStream(baos)) {
            for (Map.Entry<String, String> entry : files.entrySet()) {
                zos.putNextEntry(new ZipEntry(entry.getKey()));
                zos.write(entry.getValue().getBytes());
                zos.closeEntry();
            }
        }
        return baos.toByteArray();
    }
}
