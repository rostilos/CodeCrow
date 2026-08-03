package org.rostilos.codecrow.vcsclient.gitlab.api;

import okhttp3.OkHttpClient;
import okhttp3.mockwebserver.MockResponse;
import okhttp3.mockwebserver.MockWebServer;
import org.junit.jupiter.api.Test;

import java.io.IOException;

import static org.assertj.core.api.Assertions.assertThat;
import static org.assertj.core.api.Assertions.assertThatThrownBy;

class GitLabDiffApiTest {

    @Test
    void diffEndpointsShareConfiguredContextAndUnifiedDiffConversion()
            throws Exception {
        try (MockWebServer gitLab = new MockWebServer()) {
            gitLab.enqueue(jsonResponse("""
                    [{
                      "old_path": "old.java",
                      "new_path": "new.java",
                      "renamed_file": true,
                      "diff": "@@ -1 +1 @@\\n-old\\n+new"
                    }]
                    """).setHeader("X-Total-Pages", "1"));
            gitLab.enqueue(jsonResponse("""
                    [{
                      "old_path": "App.java",
                      "new_path": "App.java",
                      "diff": "@@ -1 +1 @@\\n-before\\n+after"
                    }]
                    """));
            gitLab.enqueue(jsonResponse("""
                    {
                      "diffs": [{
                        "old_path": "Base.java",
                        "new_path": "Base.java",
                        "diff": "@@ -1 +1 @@\\n-base\\n+head"
                      }]
                    }
                    """));
            gitLab.start();

            GitLabDiffApi diffs = new GitLabDiffApi(new GitLabApiContext(
                    new OkHttpClient(),
                    gitLab.url("/nested/gitlab").toString()));

            String mergeRequestDiff = diffs.getMergeRequestDiff(
                    "team", "repo", 17);
            String commitDiff = diffs.getCommitDiff(
                    "team", "repo", "feature/sha");
            String rangeDiff = diffs.getCommitRangeDiff(
                    "team", "repo", "base sha", "head sha");

            assertThat(mergeRequestDiff)
                    .contains("diff --git a/old.java b/new.java")
                    .contains("rename from old.java")
                    .contains("+new");
            assertThat(commitDiff).contains("+after");
            assertThat(rangeDiff).contains("+head");
            assertThat(gitLab.takeRequest().getPath())
                    .isEqualTo("/nested/gitlab/api/v4/projects/team%2Frepo"
                            + "/merge_requests/17/diffs?page=1&per_page=100");
            assertThat(gitLab.takeRequest().getPath())
                    .isEqualTo("/nested/gitlab/api/v4/projects/team%2Frepo"
                            + "/repository/commits/feature%2Fsha/diff");
            assertThat(gitLab.takeRequest().getPath())
                    .isEqualTo("/nested/gitlab/api/v4/projects/team%2Frepo"
                            + "/repository/compare?from=base%20sha&to=head%20sha");
        }
    }

    @Test
    void diffEndpointFailureUsesSharedGitLabErrorHandling() throws Exception {
        try (MockWebServer gitLab = new MockWebServer()) {
            gitLab.enqueue(new MockResponse()
                    .setResponseCode(404)
                    .setBody("Not found"));
            gitLab.start();

            GitLabDiffApi diffs = new GitLabDiffApi(new GitLabApiContext(
                    new OkHttpClient(),
                    gitLab.url("/").toString()));

            assertThatThrownBy(() -> diffs.getCommitDiff(
                    "team", "repo", "missing"))
                    .isInstanceOf(IOException.class)
                    .hasMessageContaining("404")
                    .hasMessageContaining("Not found");
        }
    }

    private static MockResponse jsonResponse(String body) {
        return new MockResponse()
                .setResponseCode(200)
                .setHeader("Content-Type", "application/json")
                .setBody(body);
    }
}
