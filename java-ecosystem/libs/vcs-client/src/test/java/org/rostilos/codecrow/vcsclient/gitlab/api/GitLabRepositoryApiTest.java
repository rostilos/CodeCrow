package org.rostilos.codecrow.vcsclient.gitlab.api;

import okhttp3.OkHttpClient;
import okhttp3.mockwebserver.MockResponse;
import okhttp3.mockwebserver.MockWebServer;
import org.junit.jupiter.api.Test;

import java.io.IOException;
import java.util.List;
import java.util.stream.Collectors;
import java.util.stream.IntStream;

import static org.assertj.core.api.Assertions.assertThat;
import static org.assertj.core.api.Assertions.assertThatThrownBy;

class GitLabRepositoryApiTest {

    @Test
    void listsOnlyBlobsAcrossRepositoryTreePages() throws Exception {
        try (MockWebServer gitLab = new MockWebServer()) {
            gitLab.start();
            String nextPage = gitLab.url(
                    "/gitlab/api/v4/projects/my%20group%2Fmy%20project"
                            + "/repository/tree?ref=commit-sha&recursive=true"
                            + "&per_page=100&pagination=keyset&page_token=cursor-2")
                    .toString();
            gitLab.enqueue(new MockResponse()
                    .setResponseCode(200)
                    .setHeader("Content-Type", "application/json")
                    .setHeader("Link", "<" + nextPage + ">; rel=\"next\"")
                    .setBody("""
                            [
                              {"path":"README.md","type":"blob","mode":"100644"},
                              {"path":"README-link","type":"blob","mode":"120000"},
                              {"path":"src","type":"tree","mode":"040000"}
                            ]
                            """));
            gitLab.enqueue(new MockResponse()
                    .setResponseCode(200)
                    .setHeader("Content-Type", "application/json")
                    .setBody("""
                            [{"path":"src/App.java","type":"blob","mode":"100755"}]
                            """));

            GitLabRepositoryApi repositories =
                    new GitLabRepositoryApi(new GitLabApiContext(
                            new OkHttpClient(),
                            gitLab.url("/gitlab").toString()));

            List<String> files = repositories.listFiles(
                    "my group", "my project", "commit-sha", 10);

            assertThat(files).containsExactly("README.md", "src/App.java");
            assertThat(gitLab.takeRequest().getPath())
                    .isEqualTo("/gitlab/api/v4/projects/my%20group%2Fmy%20project"
                            + "/repository/tree?ref=commit-sha&recursive=true"
                            + "&per_page=100&pagination=keyset");
            assertThat(gitLab.takeRequest().getPath())
                    .isEqualTo("/gitlab/api/v4/projects/my%20group%2Fmy%20project"
                            + "/repository/tree?ref=commit-sha&recursive=true"
                            + "&per_page=100&pagination=keyset&page_token=cursor-2");
        }
    }

    @Test
    void rejectsARepeatedKeysetPageBeforeIssuingAnotherRequest() throws Exception {
        try (MockWebServer gitLab = new MockWebServer()) {
            gitLab.start();
            String firstPage = gitLab.url(
                    "/api/v4/projects/team%2Frepo/repository/tree"
                            + "?ref=commit-sha&recursive=true&per_page=100"
                            + "&pagination=keyset")
                    .toString();
            gitLab.enqueue(new MockResponse()
                    .setResponseCode(200)
                    .setHeader("Content-Type", "application/json")
                    .setHeader("Link", "<" + firstPage + ">; rel=\"next\"")
                    .setBody("""
                            [{"path":"one.txt","type":"blob","mode":"100644"}]
                            """));

            GitLabRepositoryApi repositories =
                    new GitLabRepositoryApi(new GitLabApiContext(
                            new OkHttpClient(),
                            gitLab.url("/").toString()));

            assertThatThrownBy(() -> repositories.listFiles(
                    "team", "repo", "commit-sha", 10))
                    .isInstanceOf(IOException.class)
                    .hasMessageContaining("repeated a page");
            assertThat(gitLab.getRequestCount()).isEqualTo(1);
        }
    }

    @Test
    void rejectsAnOverLimitInventoryWhileWalkingTheCurrentPage() throws Exception {
        try (MockWebServer gitLab = new MockWebServer()) {
            gitLab.enqueue(new MockResponse()
                    .setResponseCode(200)
                    .setHeader("Content-Type", "application/json")
                    .setBody("""
                            [
                              {"path":"one.txt","type":"blob","mode":"100644"},
                              {"path":"two.txt","type":"blob","mode":"100644"}
                            ]
                            """));
            gitLab.start();

            GitLabRepositoryApi repositories =
                    new GitLabRepositoryApi(new GitLabApiContext(
                            new OkHttpClient(),
                            gitLab.url("/").toString()));

            assertThatThrownBy(() -> repositories.listFiles(
                    "team", "repo", "commit-sha", 1))
                    .isInstanceOf(IOException.class)
                    .hasMessageContaining("1-file inventory limit");
        }
    }

    @Test
    void boundsNonFileTreeEntriesAsWellAsReturnedFiles() throws Exception {
        try (MockWebServer gitLab = new MockWebServer()) {
            String entries = IntStream.range(0, 257)
                    .mapToObj(index -> "{\"path\":\"dir-" + index
                            + "\",\"type\":\"tree\",\"mode\":\"040000\"}")
                    .collect(Collectors.joining(",", "[", "]"));
            gitLab.enqueue(new MockResponse()
                    .setResponseCode(200)
                    .setHeader("Content-Type", "application/json")
                    .setBody(entries));
            gitLab.start();

            GitLabRepositoryApi repositories =
                    new GitLabRepositoryApi(new GitLabApiContext(
                            new OkHttpClient(),
                            gitLab.url("/").toString()));

            assertThatThrownBy(() -> repositories.listFiles(
                    "team", "repo", "commit-sha", 1))
                    .isInstanceOf(IOException.class)
                    .hasMessageContaining("256-entry traversal limit");
        }
    }

    @Test
    void fileExistenceUsesSharedEncodingAndConfiguredBase() throws Exception {
        try (MockWebServer gitLab = new MockWebServer()) {
            gitLab.enqueue(new MockResponse().setResponseCode(200));
            gitLab.enqueue(new MockResponse().setResponseCode(404));
            gitLab.start();

            GitLabRepositoryApi repositories =
                    new GitLabRepositoryApi(new GitLabApiContext(
                            new OkHttpClient(),
                            gitLab.url("/gitlab").toString()));

            assertThat(repositories.fileExists(
                    "my group",
                    "my project",
                    "feature/branch",
                    "src/folder/File.java")).isTrue();
            assertThat(repositories.fileExists(
                    "my group",
                    "my project",
                    "feature/branch",
                    "missing.java")).isFalse();

            assertThat(gitLab.takeRequest().getPath())
                    .isEqualTo("/gitlab/api/v4/projects/my%20group%2Fmy%20project"
                            + "/repository/files/src%2Ffolder%2FFile.java"
                            + "?ref=feature%2Fbranch");
        }
    }

    @Test
    void unexpectedFileResponseUsesSharedErrorHandling() throws Exception {
        try (MockWebServer gitLab = new MockWebServer()) {
            gitLab.enqueue(new MockResponse()
                    .setResponseCode(500)
                    .setBody("Failure"));
            gitLab.start();

            GitLabRepositoryApi repositories =
                    new GitLabRepositoryApi(new GitLabApiContext(
                            new OkHttpClient(),
                            gitLab.url("/").toString()));

            assertThatThrownBy(() -> repositories.fileExists(
                    "team", "repo", "main", "App.java"))
                    .isInstanceOf(IOException.class)
                    .hasMessageContaining("500");
        }
    }
}
