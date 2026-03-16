package org.rostilos.codecrow.filecontent.service;

import org.junit.jupiter.api.BeforeEach;
import org.junit.jupiter.api.Nested;
import org.junit.jupiter.api.Test;
import org.junit.jupiter.api.extension.ExtendWith;
import org.mockito.ArgumentCaptor;
import org.mockito.Mock;
import org.mockito.junit.jupiter.MockitoExtension;
import org.rostilos.codecrow.core.model.branch.Branch;
import org.rostilos.codecrow.core.model.codeanalysis.CodeAnalysis;
import org.rostilos.codecrow.core.model.pullrequest.PullRequest;
import org.rostilos.codecrow.core.persistence.repository.branch.BranchRepository;
import org.rostilos.codecrow.filecontent.model.AnalyzedFileContent;
import org.rostilos.codecrow.filecontent.model.AnalyzedFileSnapshot;
import org.rostilos.codecrow.filecontent.persistence.AnalyzedFileContentRepository;
import org.rostilos.codecrow.filecontent.persistence.AnalyzedFileSnapshotRepository;

import java.lang.reflect.Field;
import java.nio.charset.StandardCharsets;
import java.security.MessageDigest;
import java.security.NoSuchAlgorithmException;
import java.util.Collections;
import java.util.List;
import java.util.Map;
import java.util.Optional;

import static org.assertj.core.api.Assertions.assertThat;
import static org.mockito.ArgumentMatchers.*;
import static org.mockito.Mockito.*;

@ExtendWith(MockitoExtension.class)
class FileSnapshotServiceTest {

    @Mock private AnalyzedFileContentRepository contentRepository;
    @Mock private AnalyzedFileSnapshotRepository snapshotRepository;
    @Mock private BranchRepository branchRepository;

    private FileSnapshotService service;

    private static void setId(Object entity, Long id) throws Exception {
        Field f = entity.getClass().getDeclaredField("id");
        f.setAccessible(true);
        f.set(entity, id);
    }

    private AnalyzedFileContent makeContent(String hash, String content) throws Exception {
        AnalyzedFileContent fc = new AnalyzedFileContent();
        setId(fc, 100L);
        fc.setContentHash(sha256(content));
        fc.setContent(content);
        fc.setSizeBytes(content.length());
        fc.setLineCount(1);
        return fc;
    }

    private static String sha256(String input) {
        try {
            MessageDigest digest = MessageDigest.getInstance("SHA-256");
            byte[] hash = digest.digest(input.getBytes(StandardCharsets.UTF_8));
            StringBuilder hex = new StringBuilder(64);
            for (byte b : hash) {
                hex.append(String.format("%02x", b));
            }
            return hex.toString();
        } catch (NoSuchAlgorithmException e) {
            throw new RuntimeException(e);
        }
    }

    @BeforeEach
    void setUp() {
        service = new FileSnapshotService(contentRepository, snapshotRepository, branchRepository);
    }

    // ── persistSnapshots ─────────────────────────────────────────────────

    @Nested
    class PersistSnapshots {

        @Test
        void nullMap_shouldReturnZero() throws Exception {
            CodeAnalysis analysis = new CodeAnalysis();
            setId(analysis, 1L);

            int result = service.persistSnapshots(analysis, null, "abc123");

            assertThat(result).isZero();
            verifyNoInteractions(contentRepository, snapshotRepository);
        }

        @Test
        void emptyMap_shouldReturnZero() throws Exception {
            CodeAnalysis analysis = new CodeAnalysis();
            setId(analysis, 1L);

            int result = service.persistSnapshots(analysis, Collections.emptyMap(), "abc");

            assertThat(result).isZero();
        }

        @Test
        void newFiles_shouldDeduplicateAndCreateSnapshots() throws Exception {
            CodeAnalysis analysis = new CodeAnalysis();
            setId(analysis, 1L);

            // Content not found in repo → will be saved as new
            when(contentRepository.findByContentHash(anyString())).thenReturn(Optional.empty());
            when(contentRepository.save(any())).thenAnswer(inv -> {
                AnalyzedFileContent fc = inv.getArgument(0);
                setId(fc, 10L);
                return fc;
            });

            // No existing snapshot for this analysis+path
            when(snapshotRepository.findByAnalysisIdAndFilePath(eq(1L), anyString()))
                    .thenReturn(Optional.empty());

            Map<String, String> files = Map.of(
                    "src/Foo.java", "public class Foo {}",
                    "src/Bar.java", "public class Bar {}"
            );

            int result = service.persistSnapshots(analysis, files, "commit1");

            assertThat(result).isEqualTo(2);
            verify(contentRepository, times(2)).save(any());
            verify(snapshotRepository, times(2)).save(any());
        }

        @Test
        void existingContent_shouldReuseAndNotSaveContent() throws Exception {
            CodeAnalysis analysis = new CodeAnalysis();
            setId(analysis, 1L);

            AnalyzedFileContent existing = makeContent("hash1", "content");
            when(contentRepository.findByContentHash(anyString())).thenReturn(Optional.of(existing));
            when(snapshotRepository.findByAnalysisIdAndFilePath(eq(1L), anyString()))
                    .thenReturn(Optional.empty());

            int result = service.persistSnapshots(analysis, Map.of("f.java", "content"), "c1");

            assertThat(result).isEqualTo(1);
            verify(contentRepository, never()).save(any());
            verify(snapshotRepository).save(any());
        }

        @Test
        void existingSnapshot_shouldSkip() throws Exception {
            CodeAnalysis analysis = new CodeAnalysis();
            setId(analysis, 1L);

            when(contentRepository.findByContentHash(anyString())).thenReturn(Optional.empty());
            when(contentRepository.save(any())).thenAnswer(inv -> inv.getArgument(0));
            when(snapshotRepository.findByAnalysisIdAndFilePath(eq(1L), eq("f.java")))
                    .thenReturn(Optional.of(new AnalyzedFileSnapshot()));

            int result = service.persistSnapshots(analysis, Map.of("f.java", "code"), "c1");

            assertThat(result).isZero();
        }

        @Test
        void nullContent_shouldSkip() throws Exception {
            CodeAnalysis analysis = new CodeAnalysis();
            setId(analysis, 1L);

            Map<String, String> files = new java.util.HashMap<>();
            files.put("f.java", null);

            int result = service.persistSnapshots(analysis, files, "c1");

            assertThat(result).isZero();
            verifyNoInteractions(contentRepository);
        }

        @Test
        void blankFilePath_shouldSkip() throws Exception {
            CodeAnalysis analysis = new CodeAnalysis();
            setId(analysis, 1L);

            Map<String, String> files = new java.util.HashMap<>();
            files.put("  ", "some code");

            int result = service.persistSnapshots(analysis, files, "c1");

            assertThat(result).isZero();
        }
    }

    // ── updateOrPersistSnapshots ─────────────────────────────────────────

    @Nested
    class UpdateOrPersistSnapshots {

        @Test
        void nullMap_shouldReturnZero() throws Exception {
            CodeAnalysis analysis = new CodeAnalysis();
            setId(analysis, 1L);

            int result = service.updateOrPersistSnapshots(analysis, null, "c1");
            assertThat(result).isZero();
        }

        @Test
        void emptyMap_shouldReturnZero() throws Exception {
            CodeAnalysis analysis = new CodeAnalysis();
            setId(analysis, 1L);

            int result = service.updateOrPersistSnapshots(analysis, Map.of(), "c1");
            assertThat(result).isZero();
        }

        @Test
        void existingSnapshotWithDifferentContent_shouldUpdate() throws Exception {
            CodeAnalysis analysis = new CodeAnalysis();
            setId(analysis, 1L);

            AnalyzedFileContent oldContent = makeContent("old-hash", "old");
            AnalyzedFileContent newContent = makeContent("new-hash", "new code");
            when(contentRepository.findByContentHash(anyString())).thenReturn(Optional.of(newContent));

            AnalyzedFileSnapshot existingSnap = new AnalyzedFileSnapshot();
            existingSnap.setFileContent(oldContent);
            when(snapshotRepository.findByAnalysisIdAndFilePath(eq(1L), eq("f.java")))
                    .thenReturn(Optional.of(existingSnap));

            int result = service.updateOrPersistSnapshots(analysis, Map.of("f.java", "new code"), "c2");

            assertThat(result).isEqualTo(1);
            assertThat(existingSnap.getFileContent()).isSameAs(newContent);
            verify(snapshotRepository).save(existingSnap);
        }

        @Test
        void existingSnapshotWithSameContent_shouldNotUpdate() throws Exception {
            CodeAnalysis analysis = new CodeAnalysis();
            setId(analysis, 1L);

            AnalyzedFileContent content = makeContent("same-hash", "code");
            when(contentRepository.findByContentHash(anyString())).thenReturn(Optional.of(content));

            AnalyzedFileSnapshot existingSnap = new AnalyzedFileSnapshot();
            existingSnap.setFileContent(content);
            when(snapshotRepository.findByAnalysisIdAndFilePath(eq(1L), eq("f.java")))
                    .thenReturn(Optional.of(existingSnap));

            int result = service.updateOrPersistSnapshots(analysis, Map.of("f.java", "code"), "c2");

            assertThat(result).isZero();
            verify(snapshotRepository, never()).save(any());
        }

        @Test
        void noExistingSnapshot_shouldCreateNew() throws Exception {
            CodeAnalysis analysis = new CodeAnalysis();
            setId(analysis, 1L);

            when(contentRepository.findByContentHash(anyString())).thenReturn(Optional.empty());
            when(contentRepository.save(any())).thenAnswer(inv -> inv.getArgument(0));
            when(snapshotRepository.findByAnalysisIdAndFilePath(eq(1L), anyString()))
                    .thenReturn(Optional.empty());

            int result = service.updateOrPersistSnapshots(analysis, Map.of("f.java", "code"), "c2");

            assertThat(result).isEqualTo(1);
            verify(snapshotRepository).save(any());
        }
    }

    // ── Retrieval methods ────────────────────────────────────────────────

    @Nested
    class Retrieval {

        @Test
        void getSnapshotsWithContent_shouldDelegate() {
            List<AnalyzedFileSnapshot> expected = List.of(new AnalyzedFileSnapshot());
            when(snapshotRepository.findByAnalysisIdWithContent(5L)).thenReturn(expected);

            List<AnalyzedFileSnapshot> result = service.getSnapshotsWithContent(5L);

            assertThat(result).isSameAs(expected);
        }

        @Test
        void getFileContent_whenFound_shouldReturnContent() {
            AnalyzedFileContent fc = new AnalyzedFileContent();
            fc.setContent("hello world");
            AnalyzedFileSnapshot snap = new AnalyzedFileSnapshot();
            snap.setFileContent(fc);

            when(snapshotRepository.findByAnalysisIdAndFilePathWithContent(1L, "f.java"))
                    .thenReturn(Optional.of(snap));

            Optional<String> result = service.getFileContent(1L, "f.java");

            assertThat(result).contains("hello world");
        }

        @Test
        void getFileContent_whenNotFound_shouldReturnEmpty() {
            when(snapshotRepository.findByAnalysisIdAndFilePathWithContent(1L, "nope.java"))
                    .thenReturn(Optional.empty());

            assertThat(service.getFileContent(1L, "nope.java")).isEmpty();
        }

        @Test
        void getFileContentsMap_shouldBuildMap() {
            AnalyzedFileContent fc1 = new AnalyzedFileContent();
            fc1.setContent("c1");
            AnalyzedFileSnapshot s1 = new AnalyzedFileSnapshot();
            s1.setFilePath("a.java");
            s1.setFileContent(fc1);

            AnalyzedFileContent fc2 = new AnalyzedFileContent();
            fc2.setContent("c2");
            AnalyzedFileSnapshot s2 = new AnalyzedFileSnapshot();
            s2.setFilePath("b.java");
            s2.setFileContent(fc2);

            when(snapshotRepository.findByAnalysisIdWithContent(1L)).thenReturn(List.of(s1, s2));

            Map<String, String> map = service.getFileContentsMap(1L);

            assertThat(map).containsEntry("a.java", "c1").containsEntry("b.java", "c2");
        }

        @Test
        void getSnapshots_shouldDelegate() {
            when(snapshotRepository.findByAnalysisId(1L)).thenReturn(List.of());

            assertThat(service.getSnapshots(1L)).isEmpty();
        }
    }

    // ── PR-level persistence ─────────────────────────────────────────────

    @Nested
    class PrPersistence {

        @Test
        void persistSnapshotsForPr_nullMap_shouldReturnZero() throws Exception {
            PullRequest pr = new PullRequest();
            CodeAnalysis analysis = new CodeAnalysis();

            assertThat(service.persistSnapshotsForPr(pr, analysis, null, "c1")).isZero();
        }

        @Test
        void persistSnapshotsForPr_emptyMap_shouldReturnZero() throws Exception {
            PullRequest pr = new PullRequest();
            CodeAnalysis analysis = new CodeAnalysis();

            assertThat(service.persistSnapshotsForPr(pr, analysis, Map.of(), "c1")).isZero();
        }

        @Test
        void persistSnapshotsForPr_newFile_shouldCreate() throws Exception {
            PullRequest pr = new PullRequest();
            setId(pr, 10L);
            CodeAnalysis analysis = new CodeAnalysis();
            setId(analysis, 20L);

            when(contentRepository.findByContentHash(anyString())).thenReturn(Optional.empty());
            when(contentRepository.save(any())).thenAnswer(inv -> inv.getArgument(0));
            when(snapshotRepository.findByPullRequestIdAndFilePath(eq(10L), anyString()))
                    .thenReturn(Optional.empty());

            int result = service.persistSnapshotsForPr(pr, analysis, Map.of("f.java", "code"), "c1");

            assertThat(result).isEqualTo(1);
            ArgumentCaptor<AnalyzedFileSnapshot> captor = ArgumentCaptor.forClass(AnalyzedFileSnapshot.class);
            verify(snapshotRepository).save(captor.capture());
            assertThat(captor.getValue().getPullRequest()).isSameAs(pr);
            assertThat(captor.getValue().getAnalysis()).isSameAs(analysis);
        }

        @Test
        void persistSnapshotsForPr_existingWithDifferentContent_shouldUpdate() throws Exception {
            PullRequest pr = new PullRequest();
            setId(pr, 10L);
            CodeAnalysis analysis = new CodeAnalysis();
            setId(analysis, 20L);

            AnalyzedFileContent oldContent = makeContent("old-hash", "old");
            AnalyzedFileContent newContent = makeContent("new-hash", "new");
            when(contentRepository.findByContentHash(anyString())).thenReturn(Optional.of(newContent));

            AnalyzedFileSnapshot existing = new AnalyzedFileSnapshot();
            existing.setFileContent(oldContent);
            when(snapshotRepository.findByPullRequestIdAndFilePath(eq(10L), eq("f.java")))
                    .thenReturn(Optional.of(existing));

            int result = service.persistSnapshotsForPr(pr, analysis, Map.of("f.java", "new"), "c2");

            assertThat(result).isEqualTo(1);
            assertThat(existing.getFileContent()).isSameAs(newContent);
        }

        @Test
        void persistSnapshotsForPr_existingWithSameContent_shouldNotUpdate() throws Exception {
            PullRequest pr = new PullRequest();
            setId(pr, 10L);
            CodeAnalysis analysis = new CodeAnalysis();
            setId(analysis, 20L);

            AnalyzedFileContent content = makeContent("same-hash", "same");
            when(contentRepository.findByContentHash(anyString())).thenReturn(Optional.of(content));

            AnalyzedFileSnapshot existing = new AnalyzedFileSnapshot();
            existing.setFileContent(content);
            when(snapshotRepository.findByPullRequestIdAndFilePath(eq(10L), eq("f.java")))
                    .thenReturn(Optional.of(existing));

            int result = service.persistSnapshotsForPr(pr, analysis, Map.of("f.java", "same"), "c2");

            assertThat(result).isZero();
        }
    }

    // ── Branch-level persistence ─────────────────────────────────────────

    @Nested
    class BranchPersistence {

        @Test
        void persistSnapshotsForBranch_nullMap_shouldReturnZero() {
            assertThat(service.persistSnapshotsForBranch(new Branch(), null, "c1")).isZero();
        }

        @Test
        void persistSnapshotsForBranch_emptyMap_shouldReturnZero() {
            assertThat(service.persistSnapshotsForBranch(new Branch(), Map.of(), "c1")).isZero();
        }

        @Test
        void persistSnapshotsForBranch_newFile_shouldCreate() throws Exception {
            Branch branch = new Branch();
            setId(branch, 5L);

            when(contentRepository.findByContentHash(anyString())).thenReturn(Optional.empty());
            when(contentRepository.save(any())).thenAnswer(inv -> inv.getArgument(0));
            when(snapshotRepository.findByBranchIdAndFilePath(eq(5L), anyString()))
                    .thenReturn(Optional.empty());

            int result = service.persistSnapshotsForBranch(branch, Map.of("f.java", "code"), "c1");

            assertThat(result).isEqualTo(1);
            ArgumentCaptor<AnalyzedFileSnapshot> captor = ArgumentCaptor.forClass(AnalyzedFileSnapshot.class);
            verify(snapshotRepository).save(captor.capture());
            assertThat(captor.getValue().getBranch()).isSameAs(branch);
        }

        @Test
        void persistSnapshotsForBranch_existingDifferentContent_shouldUpdate() throws Exception {
            Branch branch = new Branch();
            setId(branch, 5L);

            AnalyzedFileContent oldContent = makeContent("old", "old");
            AnalyzedFileContent newContent = makeContent("new", "new");
            when(contentRepository.findByContentHash(anyString())).thenReturn(Optional.of(newContent));

            AnalyzedFileSnapshot existing = new AnalyzedFileSnapshot();
            existing.setFileContent(oldContent);
            when(snapshotRepository.findByBranchIdAndFilePath(eq(5L), eq("f.java")))
                    .thenReturn(Optional.of(existing));

            int result = service.persistSnapshotsForBranch(branch, Map.of("f.java", "new"), "c2");

            assertThat(result).isEqualTo(1);
        }

        @Test
        void persistSnapshotsForBranch_existingSameContent_shouldNotUpdate() throws Exception {
            Branch branch = new Branch();
            setId(branch, 5L);

            AnalyzedFileContent content = makeContent("same", "code");
            when(contentRepository.findByContentHash(anyString())).thenReturn(Optional.of(content));

            AnalyzedFileSnapshot existing = new AnalyzedFileSnapshot();
            existing.setFileContent(content);
            when(snapshotRepository.findByBranchIdAndFilePath(eq(5L), eq("f.java")))
                    .thenReturn(Optional.of(existing));

            int result = service.persistSnapshotsForBranch(branch, Map.of("f.java", "code"), "c2");

            assertThat(result).isZero();
        }
    }

    // ── Branch-level retrieval ───────────────────────────────────────────

    @Nested
    class BranchRetrieval {

        @Test
        void getSnapshotsForBranchById_shouldDelegate() {
            when(snapshotRepository.findByBranchId(5L)).thenReturn(List.of());
            assertThat(service.getSnapshotsForBranchById(5L)).isEmpty();
        }

        @Test
        void getSnapshotsWithContentForBranch_shouldDelegate() {
            when(snapshotRepository.findByBranchIdWithContent(5L)).thenReturn(List.of());
            assertThat(service.getSnapshotsWithContentForBranch(5L)).isEmpty();
        }

        @Test
        void getFileContentForBranchById_whenFound_shouldReturnContent() {
            AnalyzedFileContent fc = new AnalyzedFileContent();
            fc.setContent("hello");
            AnalyzedFileSnapshot snap = new AnalyzedFileSnapshot();
            snap.setFileContent(fc);

            when(snapshotRepository.findByBranchIdAndFilePathWithContent(5L, "f.java"))
                    .thenReturn(Optional.of(snap));

            assertThat(service.getFileContentForBranchById(5L, "f.java")).contains("hello");
        }

        @Test
        void getFileContentForBranchById_whenNotFound_shouldReturnEmpty() {
            when(snapshotRepository.findByBranchIdAndFilePathWithContent(5L, "x.java"))
                    .thenReturn(Optional.empty());

            assertThat(service.getFileContentForBranchById(5L, "x.java")).isEmpty();
        }

        @Test
        void getFileContentsMapForBranch_shouldBuildMap() {
            AnalyzedFileContent fc = new AnalyzedFileContent();
            fc.setContent("data");
            AnalyzedFileSnapshot snap = new AnalyzedFileSnapshot();
            snap.setFilePath("a.java");
            snap.setFileContent(fc);

            when(snapshotRepository.findByBranchIdWithContent(5L)).thenReturn(List.of(snap));

            Map<String, String> map = service.getFileContentsMapForBranch(5L);
            assertThat(map).containsEntry("a.java", "data");
        }
    }

    // ── PR-level retrieval ───────────────────────────────────────────────

    @Nested
    class PrRetrieval {

        @Test
        void getSnapshotsWithContentForPr_shouldDelegate() {
            when(snapshotRepository.findByPullRequestIdWithContent(10L)).thenReturn(List.of());
            assertThat(service.getSnapshotsWithContentForPr(10L)).isEmpty();
        }

        @Test
        void getFileContentForPr_whenFound_shouldReturnContent() {
            AnalyzedFileContent fc = new AnalyzedFileContent();
            fc.setContent("pr-content");
            AnalyzedFileSnapshot snap = new AnalyzedFileSnapshot();
            snap.setFileContent(fc);

            when(snapshotRepository.findByPullRequestIdAndFilePathWithContent(10L, "f.java"))
                    .thenReturn(Optional.of(snap));

            assertThat(service.getFileContentForPr(10L, "f.java")).contains("pr-content");
        }

        @Test
        void getFileContentForPr_whenNotFound_shouldReturnEmpty() {
            when(snapshotRepository.findByPullRequestIdAndFilePathWithContent(10L, "x.java"))
                    .thenReturn(Optional.empty());

            assertThat(service.getFileContentForPr(10L, "x.java")).isEmpty();
        }

        @Test
        void getFileContentsMapForPr_shouldBuildMap() {
            AnalyzedFileContent fc = new AnalyzedFileContent();
            fc.setContent("pr-data");
            AnalyzedFileSnapshot snap = new AnalyzedFileSnapshot();
            snap.setFilePath("a.java");
            snap.setFileContent(fc);

            when(snapshotRepository.findByPullRequestIdWithContent(10L)).thenReturn(List.of(snap));

            assertThat(service.getFileContentsMapForPr(10L)).containsEntry("a.java", "pr-data");
        }

        @Test
        void getSnapshotsForPr_shouldDelegate() {
            when(snapshotRepository.findByPullRequestId(10L)).thenReturn(List.of());
            assertThat(service.getSnapshotsForPr(10L)).isEmpty();
        }
    }

    // ── Aggregated branch retrieval ──────────────────────────────────────

    @Nested
    class AggregatedBranch {

        @Test
        void getSnapshotsForBranch_mergesBranchAndLegacy() throws Exception {
            Branch branch = new Branch();
            setId(branch, 5L);
            when(branchRepository.findByProjectIdAndBranchName(1L, "main"))
                    .thenReturn(Optional.of(branch));

            AnalyzedFileSnapshot directSnap = new AnalyzedFileSnapshot();
            directSnap.setFilePath("a.java");
            when(snapshotRepository.findByBranchId(5L)).thenReturn(List.of(directSnap));

            AnalyzedFileSnapshot legacySnap = new AnalyzedFileSnapshot();
            legacySnap.setFilePath("b.java");
            when(snapshotRepository.findLatestSnapshotsByBranch(1L, "main"))
                    .thenReturn(List.of(legacySnap));

            List<AnalyzedFileSnapshot> result = service.getSnapshotsForBranch(1L, "main");

            assertThat(result).hasSize(2);
        }

        @Test
        void getSnapshotsForBranch_directTakesPrecedenceOverLegacy() throws Exception {
            Branch branch = new Branch();
            setId(branch, 5L);
            when(branchRepository.findByProjectIdAndBranchName(1L, "main"))
                    .thenReturn(Optional.of(branch));

            AnalyzedFileSnapshot directSnap = new AnalyzedFileSnapshot();
            directSnap.setFilePath("a.java");
            when(snapshotRepository.findByBranchId(5L)).thenReturn(List.of(directSnap));

            AnalyzedFileSnapshot legacySnap = new AnalyzedFileSnapshot();
            legacySnap.setFilePath("a.java"); // same path
            when(snapshotRepository.findLatestSnapshotsByBranch(1L, "main"))
                    .thenReturn(List.of(legacySnap));

            List<AnalyzedFileSnapshot> result = service.getSnapshotsForBranch(1L, "main");

            assertThat(result).hasSize(1);
            assertThat(result.get(0)).isSameAs(directSnap);
        }

        @Test
        void getSnapshotsForBranch_noBranch_onlyLegacy() {
            when(branchRepository.findByProjectIdAndBranchName(1L, "dev"))
                    .thenReturn(Optional.empty());

            AnalyzedFileSnapshot legacySnap = new AnalyzedFileSnapshot();
            legacySnap.setFilePath("x.java");
            when(snapshotRepository.findLatestSnapshotsByBranch(1L, "dev"))
                    .thenReturn(List.of(legacySnap));

            List<AnalyzedFileSnapshot> result = service.getSnapshotsForBranch(1L, "dev");

            assertThat(result).hasSize(1);
        }

        @Test
        void getFileContentForBranch_directFkFound_shouldReturn() throws Exception {
            Branch branch = new Branch();
            setId(branch, 5L);
            when(branchRepository.findByProjectIdAndBranchName(1L, "main"))
                    .thenReturn(Optional.of(branch));

            AnalyzedFileContent fc = new AnalyzedFileContent();
            fc.setContent("direct-content");
            AnalyzedFileSnapshot snap = new AnalyzedFileSnapshot();
            snap.setFileContent(fc);
            when(snapshotRepository.findByBranchIdAndFilePathWithContent(5L, "f.java"))
                    .thenReturn(Optional.of(snap));

            assertThat(service.getFileContentForBranch(1L, "main", "f.java"))
                    .contains("direct-content");
        }

        @Test
        void getFileContentForBranch_fallsBackToLegacy() throws Exception {
            Branch branch = new Branch();
            setId(branch, 5L);
            when(branchRepository.findByProjectIdAndBranchName(1L, "main"))
                    .thenReturn(Optional.of(branch));

            when(snapshotRepository.findByBranchIdAndFilePathWithContent(5L, "f.java"))
                    .thenReturn(Optional.empty());

            AnalyzedFileContent fc = new AnalyzedFileContent();
            fc.setContent("legacy-content");
            AnalyzedFileSnapshot legacySnap = new AnalyzedFileSnapshot();
            legacySnap.setFileContent(fc);
            when(snapshotRepository.findLatestSnapshotByBranchAndFilePath(1L, "main", "f.java"))
                    .thenReturn(Optional.of(legacySnap));

            assertThat(service.getFileContentForBranch(1L, "main", "f.java"))
                    .contains("legacy-content");
        }

        @Test
        void getFileContentForBranch_noBranch_fallsBackToLegacy() {
            when(branchRepository.findByProjectIdAndBranchName(1L, "dev"))
                    .thenReturn(Optional.empty());

            AnalyzedFileContent fc = new AnalyzedFileContent();
            fc.setContent("legacy-only");
            AnalyzedFileSnapshot legacySnap = new AnalyzedFileSnapshot();
            legacySnap.setFileContent(fc);
            when(snapshotRepository.findLatestSnapshotByBranchAndFilePath(1L, "dev", "f.java"))
                    .thenReturn(Optional.of(legacySnap));

            assertThat(service.getFileContentForBranch(1L, "dev", "f.java"))
                    .contains("legacy-only");
        }

        @Test
        void getFileContentForBranch_notFoundAnywhere_shouldReturnEmpty() {
            when(branchRepository.findByProjectIdAndBranchName(1L, "main"))
                    .thenReturn(Optional.empty());
            when(snapshotRepository.findLatestSnapshotByBranchAndFilePath(1L, "main", "f.java"))
                    .thenReturn(Optional.empty());

            assertThat(service.getFileContentForBranch(1L, "main", "f.java")).isEmpty();
        }
    }

    // ── Source availability ──────────────────────────────────────────────

    @Nested
    class SourceAvailability {

        @Test
        void getBranchesWithSnapshots_shouldDelegate() {
            when(snapshotRepository.findBranchNamesWithSnapshots(1L))
                    .thenReturn(List.of("main", "dev"));

            assertThat(service.getBranchesWithSnapshots(1L)).containsExactly("main", "dev");
        }

        @Test
        void getPrNumbersWithSnapshots_shouldDelegate() {
            when(snapshotRepository.findPrNumbersWithSnapshots(1L))
                    .thenReturn(List.of(10L, 20L));

            assertThat(service.getPrNumbersWithSnapshots(1L)).containsExactly(10L, 20L);
        }
    }
}
