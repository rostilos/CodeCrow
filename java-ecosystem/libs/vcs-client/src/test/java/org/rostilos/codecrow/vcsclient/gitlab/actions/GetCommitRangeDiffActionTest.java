package org.rostilos.codecrow.vcsclient.gitlab.actions;

import com.fasterxml.jackson.databind.ObjectMapper;
import okhttp3.OkHttpClient;
import okhttp3.Request;
import okhttp3.mockwebserver.MockResponse;
import okhttp3.mockwebserver.MockWebServer;
import org.junit.jupiter.api.AfterEach;
import org.junit.jupiter.api.BeforeEach;
import org.junit.jupiter.api.Test;

import java.io.IOException;
import java.util.List;
import java.util.Map;

import static org.assertj.core.api.Assertions.assertThat;
import static org.assertj.core.api.Assertions.assertThatThrownBy;

class GetCommitRangeDiffActionTest {
    private MockWebServer server;
    private GetCommitRangeDiffAction action;

    @BeforeEach
    void setUp() throws IOException {
        server = new MockWebServer();
        server.start();
        OkHttpClient client = new OkHttpClient.Builder()
                .addInterceptor(chain -> {
                    Request original = chain.request();
                    String path = original.url().encodedPath();
                    String query = original.url().encodedQuery();
                    return chain.proceed(original.newBuilder()
                            .url(server.url(path + (query != null ? "?" + query : "")))
                            .build());
                })
                .build();
        action = new GetCommitRangeDiffAction(client);
    }

    @AfterEach
    void tearDown() throws IOException {
        server.shutdown();
    }

    @Test
    void returnsCompletePatchContent() throws Exception {
        server.enqueue(json("""
                {"diffs":[{"old_path":"file.java","new_path":"file.java",
                "diff":"@@ -1 +1 @@\\n-old\\n+new"}]}
                """));

        String result = action.getCommitRangeDiff("namespace", "project", "abc123", "def456");

        assertThat(result).contains("diff --git a/file.java b/file.java", "@@ -1 +1 @@");
        assertThat(server.takeRequest().getPath())
                .contains("from=abc123", "to=def456");
    }

    @Test
    void failsClosedOnCompareTimeout() {
        server.enqueue(json("{\"compare_timeout\":true,\"diffs\":[]}"));

        assertThatThrownBy(() -> action.getCommitRangeDiff("ns", "repo", "base", "head"))
                .isInstanceOf(IOException.class)
                .hasMessageContaining("timed out");
    }

    @Test
    void failsClosedWhenDiffsArrayIsMissingOrMalformed() {
        server.enqueue(json("{}"));
        server.enqueue(json("{\"diffs\":{}}"));

        assertThatThrownBy(() -> action.getCommitRangeDiff("ns", "repo", "base", "head"))
                .isInstanceOf(IOException.class)
                .hasMessageContaining("diffs array");
        assertThatThrownBy(() -> action.getCommitRangeDiff("ns", "repo", "base", "head"))
                .isInstanceOf(IOException.class)
                .hasMessageContaining("diffs array");
    }

    @Test
    void failsClosedOnCollapsedOrOversizedEntry() {
        server.enqueue(json("""
                {"diffs":[{"old_path":"a","new_path":"a","collapsed":true,"diff":""}]}
                """));

        assertThatThrownBy(() -> action.getCommitRangeDiff("ns", "repo", "base", "head"))
                .isInstanceOf(IOException.class)
                .hasMessageContaining("collapsed or oversized");
    }

    @Test
    void failsClosedWhenTextualEntryHasNoPatch() {
        server.enqueue(json("""
                {"diffs":[{"old_path":"a.java","new_path":"a.java","diff":""}]}
                """));

        assertThatThrownBy(() -> action.getCommitRangeDiff("ns", "repo", "base", "head"))
                .isInstanceOf(IOException.class)
                .hasMessageContaining("omitted patch content");
    }

    @Test
    void quotesSpecialAndUtf8PathsInSynthesizedDiff() throws Exception {
        String path = "src/My \"日本\\File.java";
        String body = new ObjectMapper().writeValueAsString(Map.of(
                "diffs", List.of(Map.of(
                        "old_path", path,
                        "new_path", path,
                        "diff", "@@ -1 +1 @@\n-old\n+new"))));
        server.enqueue(json(body));

        String result = action.getCommitRangeDiff("ns", "repo", "base", "head");

        assertThat(result)
                .contains("diff --git \"a/src/My \\\"日本\\\\File.java\"")
                .contains("--- \"a/src/My \\\"日本\\\\File.java\"")
                .contains("+++ \"b/src/My \\\"日本\\\\File.java\"");
    }

    @Test
    void metadataOnlyRenameDoesNotInventTextualMarkers() throws Exception {
        server.enqueue(json("""
                {"diffs":[{"old_path":"old.java","new_path":"new.java",
                "renamed_file":true,"diff":""}]}
                """));

        String result = action.getCommitRangeDiff("ns", "repo", "base", "head");

        assertThat(result)
                .contains("rename from old.java", "rename to new.java")
                .doesNotContain("similarity index")
                .doesNotContain("--- ", "+++ ");
    }

    @Test
    void metadataOnlyModeChangeDoesNotInventTextualMarkers() throws Exception {
        server.enqueue(json("""
                {"diffs":[{"old_path":"script.sh","new_path":"script.sh",
                "a_mode":"100644","b_mode":"100755","diff":""}]}
                """));

        String result = action.getCommitRangeDiff("ns", "repo", "base", "head");

        assertThat(result)
                .contains("old mode 100644", "new mode 100755")
                .doesNotContain("--- ", "+++ ");
    }

    @Test
    void emptyAddedAndDeletedFilesRemainMetadataOnly() throws Exception {
        server.enqueue(json("""
                {"diffs":[
                  {"old_path":"empty.txt","new_path":"empty.txt","new_file":true,
                   "a_mode":"000000","b_mode":"100644","diff":""},
                  {"old_path":"gone.sh","new_path":"gone.sh","deleted_file":true,
                   "a_mode":"100755","b_mode":"000000","diff":""}
                ]}
                """));

        String result = action.getCommitRangeDiff("ns", "repo", "base", "head");

        assertThat(result)
                .contains("new file mode 100644", "deleted file mode 100755")
                .doesNotContain("--- ", "+++ ");
    }

    @Test
    void propagatesProviderError() {
        server.enqueue(new MockResponse().setResponseCode(404).setBody("Not found"));

        assertThatThrownBy(() -> action.getCommitRangeDiff("ns", "repo", "base", "head"))
                .isInstanceOf(IOException.class)
                .hasMessageContaining("404");
    }

    private MockResponse json(String body) {
        return new MockResponse().setHeader("Content-Type", "application/json").setBody(body);
    }
}
