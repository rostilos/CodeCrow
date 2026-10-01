package org.rostilos.codecrow.mcp;

import org.junit.jupiter.api.AfterEach;
import org.junit.jupiter.api.Test;
import org.junit.jupiter.api.io.TempDir;
import org.rostilos.codecrow.mcp.generic.VcsMcpClient;
import org.rostilos.codecrow.mcp.generic.VcsMcpClientFactory;

import java.nio.file.Files;
import java.nio.file.Path;
import java.util.ArrayList;
import java.util.List;
import java.util.Map;
import java.util.concurrent.CountDownLatch;
import java.util.concurrent.ExecutorService;
import java.util.concurrent.Executors;
import java.util.concurrent.Future;
import java.util.concurrent.TimeUnit;

import static org.assertj.core.api.Assertions.assertThat;
import static org.mockito.Mockito.mock;
import static org.mockito.Mockito.never;
import static org.mockito.Mockito.verify;
import static org.mockito.Mockito.when;

class McpToolsLocalRepoTest {

    @TempDir
    Path repository;

    @AfterEach
    void clearLocalRepositoryProperties() {
        System.clearProperty(McpTools.LOCAL_REPO_PATH_PROPERTY);
        System.clearProperty(McpTools.LOCAL_REPO_TARGET_BRANCH_PROPERTY);
        System.clearProperty(McpTools.LOCAL_REPO_REVISION_PROPERTY);
        System.clearProperty(McpTools.LOCAL_REVIEW_OVERLAY_PATH_PROPERTY);
        System.clearProperty(McpTools.LOCAL_MCP_ONLY_PROPERTY);
        System.clearProperty(McpTools.WORKSPACE_PROPERTY);
        System.clearProperty(McpTools.REPO_SLUG_PROPERTY);
    }

    @Test
    void localOnlyModeKeepsProposedSourceAndBlocksEveryProviderFallback() throws Exception {
        Files.createDirectories(repository.resolve("src"));
        Files.writeString(repository.resolve("src/Unchanged.java"), "class Unchanged {}\n");
        Path overlay = repository.resolve("review-overlay");
        Files.createDirectories(overlay.resolve("files/src"));
        Files.writeString(
                overlay.resolve("files/src/Changed.java"),
                "class Changed { int proposed; }\n");
        Files.writeString(
                overlay.resolve("manifest.json"),
                "{\"changedFiles\":[\"src/Changed.java\"],\"deletedFiles\":[]}");
        System.setProperty(McpTools.LOCAL_REPO_PATH_PROPERTY, repository.toString());
        System.setProperty(McpTools.LOCAL_REPO_TARGET_BRANCH_PROPERTY, "main");
        System.setProperty(McpTools.LOCAL_REPO_REVISION_PROPERTY, "target-head-sha");
        System.setProperty(McpTools.LOCAL_REVIEW_OVERLAY_PATH_PROPERTY, overlay.toString());
        System.setProperty(McpTools.LOCAL_MCP_ONLY_PROPERTY, "true");
        System.setProperty(McpTools.WORKSPACE_PROPERTY, "team");
        System.setProperty(McpTools.REPO_SLUG_PROPERTY, "repo");

        VcsMcpClientFactory factory = mock(VcsMcpClientFactory.class);
        McpTools tools = new McpTools(factory);

        @SuppressWarnings("unchecked")
        Map<String, Object> proposed = (Map<String, Object>) tools.execute(
                "getReviewFileContent",
                Map.of(
                        "workspace", "team",
                        "repoSlug", "repo",
                        "filePath", "src/Changed.java"));
        @SuppressWarnings("unchecked")
        Map<String, Object> comments = (Map<String, Object>) tools.execute(
                "getPullRequestComments",
                Map.of(
                        "workspace", "team",
                        "repoSlug", "repo",
                        "pullRequestId", "42"));
        @SuppressWarnings("unchecked")
        Map<String, Object> wrongRef = (Map<String, Object>) tools.execute(
                "getBranchFileContent",
                Map.of(
                        "workspace", "team",
                        "repoSlug", "repo",
                        "branch", "provider-branch",
                        "filePath", "src/Unchanged.java"));
        @SuppressWarnings("unchecked")
        Map<String, Object> missingLocal = (Map<String, Object>) tools.execute(
                "getBranchFileContent",
                Map.of(
                        "workspace", "team",
                        "repoSlug", "repo",
                        "branch", "main",
                        "filePath", "src/Missing.java"));
        @SuppressWarnings("unchecked")
        Map<String, Object> missingUnchangedReview = (Map<String, Object>) tools.execute(
                "getReviewFileContent",
                Map.of(
                        "workspace", "team",
                        "repoSlug", "repo",
                        "filePath", "src/Missing.java"));

        assertThat(proposed)
                .containsEntry("source", "review-overlay")
                .containsEntry("filePath", "src/Changed.java")
                .containsEntry("changed", true);
        assertThat(comments.get("error").toString())
                .contains("Provider-backed VCS tools are disabled");
        assertThat(wrongRef.get("error").toString())
                .contains("Provider-backed VCS tools are disabled");
        assertThat(missingLocal.get("error").toString())
                .contains("Provider-backed VCS tools are disabled");
        assertThat(missingUnchangedReview.get("error").toString())
                .contains("Provider-backed VCS tools are disabled");
        verify(factory, never()).createClient();
    }

    @Test
    void directoryDispatchUsesLocalSnapshotAndKeepsRemoteClientForProviderTools() throws Exception {
        Files.createDirectories(repository.resolve("src/main"));
        Files.writeString(repository.resolve("src/App.java"), "class App {}");
        System.setProperty(McpTools.LOCAL_REPO_PATH_PROPERTY, repository.toString());
        System.setProperty(McpTools.LOCAL_REPO_TARGET_BRANCH_PROPERTY, "main");
        System.setProperty(McpTools.LOCAL_REPO_REVISION_PROPERTY, "target-head-sha");
        System.setProperty(McpTools.WORKSPACE_PROPERTY, "team");
        System.setProperty(McpTools.REPO_SLUG_PROPERTY, "repo");

        VcsMcpClientFactory factory = mock(VcsMcpClientFactory.class);
        VcsMcpClient remote = mock(VcsMcpClient.class);
        when(factory.createClient()).thenReturn(remote);
        when(remote.getPullRequestComments("team", "repo", "42"))
                .thenReturn(Map.of("comments", 1));
        McpTools tools = new McpTools(factory);

        @SuppressWarnings("unchecked")
        Map<String, Object> directoryResult = (Map<String, Object>) tools.execute(
                "getDirectoryByPath",
                Map.of(
                        "workspace", "team",
                        "projectKey", "repo",
                        "branch", "main",
                        "dirPath", "src"));
        @SuppressWarnings("unchecked")
        Map<String, Object> commentsResult = (Map<String, Object>) tools.execute(
                "getPullRequestComments",
                Map.of(
                        "workspace", "team",
                        "repoSlug", "repo",
                        "pullRequestId", "42"));

        assertThat(directoryResult).containsEntry("directoryContent", "App.java\nmain/");
        assertThat(commentsResult).containsEntry("comments", Map.of("comments", 1));
        verify(remote, never()).getDirectoryByPath("team", "repo", "main", "src");
        verify(remote).getPullRequestComments("team", "repo", "42");
    }

    @Test
    void defaultModeFallsBackToPinnedRevisionForMissingPaths() throws Exception {
        Files.createDirectories(repository.resolve("src"));
        System.setProperty(McpTools.LOCAL_REPO_PATH_PROPERTY, repository.toString());
        System.setProperty(McpTools.LOCAL_REPO_TARGET_BRANCH_PROPERTY, "main");
        System.setProperty(McpTools.LOCAL_REPO_REVISION_PROPERTY, "target-head-sha");
        System.setProperty(McpTools.WORKSPACE_PROPERTY, "team");
        System.setProperty(McpTools.REPO_SLUG_PROPERTY, "repo");

        VcsMcpClientFactory factory = mock(VcsMcpClientFactory.class);
        VcsMcpClient remote = mock(VcsMcpClient.class);
        when(factory.createClient()).thenReturn(remote);
        when(remote.getBranchFileContent(
                "team", "repo", "target-head-sha", "src/Missing.java"))
                .thenReturn("remote source");
        when(remote.getDirectoryByPath(
                "team", "repo", "target-head-sha", "missing"))
                .thenReturn("Generated.java");
        McpTools tools = new McpTools(factory);

        @SuppressWarnings("unchecked")
        Map<String, Object> file = (Map<String, Object>) tools.execute(
                "getBranchFileContent",
                Map.of(
                        "workspace", "team",
                        "repoSlug", "repo",
                        "branch", "main",
                        "filePath", "src/Missing.java"));
        @SuppressWarnings("unchecked")
        Map<String, Object> directory = (Map<String, Object>) tools.execute(
                "getDirectoryByPath",
                Map.of(
                        "workspace", "team",
                        "projectKey", "repo",
                        "branch", "main",
                        "dirPath", "missing"));

        assertThat(file).containsEntry("fileContent", "remote source");
        assertThat(directory).containsEntry("directoryContent", "Generated.java");
        verify(remote).getBranchFileContent(
                "team", "repo", "target-head-sha", "src/Missing.java");
        verify(remote).getDirectoryByPath(
                "team", "repo", "target-head-sha", "missing");
    }

    @Test
    void repositoryReadToolsRejectArgumentsOutsideTheRequestBinding() throws Exception {
        System.setProperty(McpTools.WORKSPACE_PROPERTY, "team");
        System.setProperty(McpTools.REPO_SLUG_PROPERTY, "repo");
        VcsMcpClientFactory factory = mock(VcsMcpClientFactory.class);
        VcsMcpClient remote = mock(VcsMcpClient.class);
        when(factory.createClient()).thenReturn(remote);
        McpTools tools = new McpTools(factory);

        @SuppressWarnings("unchecked")
        Map<String, Object> result = (Map<String, Object>) tools.execute(
                "getBranchFileContent",
                Map.of(
                        "workspace", "other-team",
                        "repoSlug", "other-repo",
                        "branch", "main",
                        "filePath", "src/App.java"));

        assertThat(result.get("error").toString())
                .contains("request-bound repository");
        verify(remote, never()).getBranchFileContent(
                "other-team", "other-repo", "main", "src/App.java");
    }

    @Test
    void reviewFileToolReturnsProposedContentAndExplicitDeletionState() throws Exception {
        Files.createDirectories(repository.resolve("src"));
        Files.writeString(repository.resolve("src/Unchanged.java"), "class Unchanged {}\n");
        Path overlay = repository.resolve("review-overlay");
        Files.createDirectories(overlay.resolve("files/src"));
        Files.writeString(
                overlay.resolve("files/src/Changed.java"),
                "class Changed { int proposed; }\n");
        Files.writeString(
                overlay.resolve("manifest.json"),
                "{\"changedFiles\":[\"src/Changed.java\",\"src/Deleted.java\"],"
                        + "\"deletedFiles\":[\"src/Deleted.java\"]}");
        System.setProperty(McpTools.LOCAL_REPO_PATH_PROPERTY, repository.toString());
        System.setProperty(McpTools.LOCAL_REPO_TARGET_BRANCH_PROPERTY, "main");
        System.setProperty(McpTools.LOCAL_REPO_REVISION_PROPERTY, "target-head-sha");
        System.setProperty(McpTools.LOCAL_REVIEW_OVERLAY_PATH_PROPERTY, overlay.toString());
        System.setProperty(McpTools.WORKSPACE_PROPERTY, "team");
        System.setProperty(McpTools.REPO_SLUG_PROPERTY, "repo");
        VcsMcpClientFactory factory = mock(VcsMcpClientFactory.class);
        when(factory.createClient()).thenThrow(
                new IllegalStateException(
                        "accessToken system property is required for GitHub"));
        McpTools tools = new McpTools(factory);

        @SuppressWarnings("unchecked")
        Map<String, Object> changed = (Map<String, Object>) tools.execute(
                "getReviewFileContent",
                Map.of(
                        "workspace", "team",
                        "repoSlug", "repo",
                        "filePath", "src/Changed.java"));
        @SuppressWarnings("unchecked")
        Map<String, Object> deleted = (Map<String, Object>) tools.execute(
                "getReviewFileContent",
                Map.of(
                        "workspace", "team",
                        "repoSlug", "repo",
                        "filePath", "src/Deleted.java"));
        @SuppressWarnings("unchecked")
        Map<String, Object> unchanged = (Map<String, Object>) tools.execute(
                "getReviewFileContent",
                Map.of(
                        "workspace", "team",
                        "repoSlug", "repo",
                        "filePath", "src/Unchanged.java"));

        assertThat(changed)
                .containsEntry("fileContent", "class Changed { int proposed; }\n")
                .containsEntry("source", "review-overlay")
                .containsEntry("changed", true);
        assertThat(deleted)
                .containsEntry("exists", false)
                .containsEntry("deleted", true);
        assertThat(unchanged)
                .containsEntry("fileContent", "class Unchanged {}\n")
                .containsEntry("source", "target-head")
                .containsEntry("changed", false);
        verify(factory, never()).createClient();
    }

    @Test
    void reviewFileToolRejectsOnlyRedundantWholeFileReadsFromPromptContext() throws Exception {
        Files.createDirectories(repository.resolve("src"));
        Files.writeString(
                repository.resolve("src/Changed.java"),
                "line one\nline two\n");
        Files.writeString(
                repository.resolve("src/Related.java"),
                "related source\n");
        Path overlay = repository.resolve("review-overlay");
        Files.createDirectories(overlay.resolve("files/src"));
        Files.writeString(
                overlay.resolve("files/src/Changed.java"),
                "line one\nline two\n");
        Files.writeString(
                overlay.resolve("manifest.json"),
                "{\"changedFiles\":[\"src/Changed.java\"],\"deletedFiles\":[]}");
        System.setProperty(McpTools.LOCAL_REPO_PATH_PROPERTY, repository.toString());
        System.setProperty(McpTools.LOCAL_REPO_TARGET_BRANCH_PROPERTY, "main");
        System.setProperty(McpTools.LOCAL_REPO_REVISION_PROPERTY, "target-head-sha");
        System.setProperty(McpTools.LOCAL_REVIEW_OVERLAY_PATH_PROPERTY, overlay.toString());
        System.setProperty(McpTools.WORKSPACE_PROPERTY, "team");
        System.setProperty(McpTools.REPO_SLUG_PROPERTY, "repo");

        VcsMcpClientFactory factory = mock(VcsMcpClientFactory.class);
        McpTools tools = new McpTools(factory);
        Map<String, Object> binding = Map.of(
                "workspace", "team",
                "repoSlug", "repo",
                "filePath", "src/Changed.java",
                "contextSuppliedPaths", List.of("src/Changed.java"));

        @SuppressWarnings("unchecked")
        Map<String, Object> redundant = (Map<String, Object>) tools.execute(
                "getReviewFileContent",
                binding);
        @SuppressWarnings("unchecked")
        Map<String, Object> bounded = (Map<String, Object>) tools.execute(
                "getReviewFileContent",
                Map.of(
                        "workspace", "team",
                        "repoSlug", "repo",
                        "filePath", "src/Changed.java",
                        "startLine", 2,
                        "endLine", 2,
                        "contextSuppliedPaths", List.of("src/Changed.java")));
        @SuppressWarnings("unchecked")
        Map<String, Object> related = (Map<String, Object>) tools.execute(
                "getReviewFileContent",
                Map.of(
                        "workspace", "team",
                        "repoSlug", "repo",
                        "filePath", "src/Related.java",
                        "contextSuppliedPaths", List.of("src/Changed.java")));

        assertThat(redundant)
                .containsEntry("errorCode", "current_source_already_supplied")
                .containsEntry("filePath", "src/Changed.java");
        assertThat(bounded)
                .containsEntry("fileContent", "line two")
                .containsEntry("startLine", 2)
                .containsEntry("endLine", 2);
        assertThat(related)
                .containsEntry("fileContent", "related source\n")
                .containsEntry("completeFile", true);
        verify(factory, never()).createClient();
    }

    @Test
    void concurrentLocalReadsNeverFallThroughDuringClientResolution() throws Exception {
        Files.createDirectories(repository.resolve("src"));
        Files.writeString(repository.resolve("src/App.java"), "class App {}\n");
        System.setProperty(McpTools.LOCAL_REPO_PATH_PROPERTY, repository.toString());
        System.setProperty(McpTools.LOCAL_REPO_TARGET_BRANCH_PROPERTY, "main");
        System.setProperty(McpTools.LOCAL_REPO_REVISION_PROPERTY, "target-head-sha");
        System.setProperty(McpTools.WORKSPACE_PROPERTY, "team");
        System.setProperty(McpTools.REPO_SLUG_PROPERTY, "repo");
        VcsMcpClientFactory factory = mock(VcsMcpClientFactory.class);
        when(factory.createClient()).thenThrow(
                new IllegalStateException(
                        "accessToken system property is required for GitHub"));
        McpTools tools = new McpTools(factory);

        int callerCount = 24;
        ExecutorService executor = Executors.newFixedThreadPool(callerCount);
        CountDownLatch ready = new CountDownLatch(callerCount);
        CountDownLatch start = new CountDownLatch(1);
        List<Future<Object>> results = new ArrayList<>();
        try {
            for (int index = 0; index < callerCount; index++) {
                results.add(executor.submit(() -> {
                    ready.countDown();
                    if (!start.await(5, TimeUnit.SECONDS)) {
                        throw new IllegalStateException("Concurrent read start timed out");
                    }
                    return tools.execute(
                            "getBranchFileContent",
                            Map.of(
                                    "workspace", "team",
                                    "repoSlug", "repo",
                                    "branch", "main",
                                    "filePath", "src/App.java"));
                }));
            }
            assertThat(ready.await(5, TimeUnit.SECONDS)).isTrue();
            start.countDown();
            for (Future<Object> result : results) {
                assertThat(result.get(10, TimeUnit.SECONDS))
                        .isInstanceOf(Map.class)
                        .asInstanceOf(org.assertj.core.api.InstanceOfAssertFactories.MAP)
                        .containsEntry("fileContent", "class App {}\n");
            }
        } finally {
            start.countDown();
            executor.shutdownNow();
        }

        verify(factory, never()).createClient();
    }
}
