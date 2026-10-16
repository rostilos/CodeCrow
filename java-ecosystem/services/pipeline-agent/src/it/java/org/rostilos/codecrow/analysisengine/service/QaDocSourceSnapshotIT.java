package org.rostilos.codecrow.analysisengine.service;

import com.fasterxml.jackson.databind.ObjectMapper;
import okhttp3.mockwebserver.*;
import org.junit.jupiter.params.ParameterizedTest;
import org.junit.jupiter.api.Test;
import org.junit.jupiter.params.provider.EnumSource;
import org.rostilos.codecrow.core.model.project.Project;
import org.rostilos.codecrow.core.model.vcs.*;
import org.rostilos.codecrow.pipelineagent.qadoc.*;
import org.rostilos.codecrow.security.oauth.TokenEncryptionService;
import org.rostilos.codecrow.vcsclient.*;
import java.io.IOException;
import java.nio.file.*;
import java.util.*;
import java.util.concurrent.atomic.AtomicReference;
import static org.assertj.core.api.Assertions.*;
import static org.mockito.Mockito.*;
import static org.mockito.ArgumentMatchers.*;

/** Real HTTP and snapshot/overlay filesystem lifecycle; provider transport is isolated. */
class QaDocSourceSnapshotIT {
    @ParameterizedTest @EnumSource(EVcsProvider.class)
    void snapshotLivesThroughInferenceAndIsRemovedAfterSuccess(EVcsProvider provider) throws Exception { exercise(provider, false); }
    @ParameterizedTest @EnumSource(EVcsProvider.class)
    void snapshotIsRemovedAfterInferenceFailure(EVcsProvider provider) throws Exception { exercise(provider, true); }

    @Test void sourceFallbackAlsoOwnsAFreshSnapshotWhenSemanticScanningFailed() throws Exception { exercise(EVcsProvider.GITHUB, false, false); }

    private void exercise(EVcsProvider provider, boolean fail) throws Exception { exercise(provider, fail, true); }

    private void exercise(EVcsProvider provider, boolean fail, boolean savedSummary) throws Exception {
        var archives = mock(BranchArchiveService.class); var clients = mock(VcsClientProvider.class);
        var vcs = mock(VcsClient.class); var connection = mock(VcsConnection.class);
        when(connection.getProviderType()).thenReturn(provider); when(clients.getClient(connection)).thenReturn(vcs);
        when(vcs.getFileContent("workspace", "repo", "src/A.java", "reviewed-head")).thenReturn("reviewed proposed content");
        Path scratch = Files.createDirectories(Path.of("target", "qa-source-snapshot-it"));
        var snapshots = new LocalRepositorySnapshotService(archives, clients, scratch);
        doAnswer(inv -> { Files.writeString(((Path) inv.getArgument(5)).resolve("unchanged.java"), "target content"); return null; })
                .when(archives).downloadAndExtractSnapshotToDirectory(eq(connection), eq("workspace"), eq("repo"), eq("target-head"), isNull(), any(Path.class));
        var project = mock(Project.class); var repo = mock(VcsRepoInfo.class);
        when(project.getId()).thenReturn(42L); when(project.getEffectiveVcsRepoInfo()).thenReturn(repo);
        when(project.getEffectiveConfig()).thenReturn(new org.rostilos.codecrow.core.model.project.config.ProjectConfig());
        when(repo.getVcsConnection()).thenReturn(connection); when(repo.getRepoWorkspace()).thenReturn("workspace"); when(repo.getRepoSlug()).thenReturn("repo");
        var source = new AtomicReference<Path>(); var overlay = new AtomicReference<Path>();
        var mapper = new ObjectMapper();
        try (var server = new MockWebServer()) {
            server.setDispatcher(new Dispatcher() {
                @Override public MockResponse dispatch(RecordedRequest request) {
                    try {
                        var binding = mapper.readTree(request.getBody().readUtf8()).at("/tool_context/repository");
                        source.set(Path.of(binding.path("localRepoPath").asText()));
                        overlay.set(Path.of(binding.path("localReviewOverlayPath").asText()));
                        assertThat(source.get()).isDirectory(); assertThat(overlay.get()).isDirectory();
                        assertThat(Files.readString(overlay.get().resolve("files/src/A.java"))).isEqualTo("reviewed proposed content");
                        assertThat(Files.readString(overlay.get().resolve("manifest.json"))).contains("src/deleted.java");
                        assertThat(binding.path("localRepoRevision").asText()).isEqualTo("target-head");
                        return fail ? new MockResponse().setResponseCode(422) : new MockResponse().setBody("{\"documentation\":\"QA coverage\"}");
                    } catch (Throwable failure) {
                        return new MockResponse().setResponseCode(422).setBody(failure.toString());
                    }
                }
            });
            server.start();
            var service = new QaDocGenerationService(server.url("").toString().replaceAll("/$", ""), "secret",
                    mock(TokenEncryptionService.class), snapshots, clients);
            var savedBinding = Map.<String,Object>of("localRepoPath", "/deleted/review", "localReviewOverlayPath", "/deleted/overlay",
                    "localRepoRevision", "target-head", "localRepoTargetBranch", "main");
            var summary = Map.<String,Object>of("projectId", 42L, "pullRequestId", 7L, "sourceRevision", "reviewed-head",
                    "files", List.of(Map.of("path", "src/A.java", "changeType", "modified"), Map.of("path", "src/deleted.java", "changeType", "deleted")),
                    "toolContext", Map.of("repository", savedBinding));
            var ctx = QaDocGenerationContext.builder().sourceRevision("reviewed-head").targetBranch("main").prChangeSummary(savedSummary ? summary : null)
                    .reviewSource(Map.of("targetRevision", "target-head", "changedFiles", List.of("src/A.java", "src/deleted.java"),
                            "deletedFiles", List.of("src/deleted.java"))).build();
            if (fail) assertThatThrownBy(() -> service.generateQaDocumentation(project, 7L, 0, 2, Map.of(), ctx)).isInstanceOf(IOException.class);
            else assertThat(service.generateQaDocumentation(project, 7L, 0, 2, Map.of(), ctx)).isEqualTo("QA coverage");
            assertThat(source.get()).isNotNull().doesNotExist(); assertThat(overlay.get()).isNotNull().doesNotExist();
            assertThat(savedBinding.get("localRepoPath")).isEqualTo("/deleted/review");
            verify(vcs).getFileContent("workspace", "repo", "src/A.java", "reviewed-head");
            verify(vcs, never()).getFileContent(anyString(), anyString(), eq("src/deleted.java"), anyString());
            verify(vcs, never()).getLatestCommitHash(anyString(), anyString(), anyString());
        }
    }
}
