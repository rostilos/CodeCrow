package org.rostilos.codecrow.vcsclient.gitlab.api;

import okhttp3.OkHttpClient;
import okhttp3.mockwebserver.MockResponse;
import okhttp3.mockwebserver.MockWebServer;
import org.junit.jupiter.api.Test;

import java.io.IOException;

import static org.assertj.core.api.Assertions.assertThat;
import static org.assertj.core.api.Assertions.assertThatThrownBy;

class GitLabRepositoryApiTest {

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
