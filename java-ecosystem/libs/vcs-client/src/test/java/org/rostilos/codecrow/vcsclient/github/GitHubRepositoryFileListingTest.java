package org.rostilos.codecrow.vcsclient.github;

import okhttp3.Call;
import okhttp3.MediaType;
import okhttp3.OkHttpClient;
import okhttp3.Protocol;
import okhttp3.Request;
import okhttp3.Response;
import okhttp3.ResponseBody;
import org.junit.jupiter.api.Test;
import org.junit.jupiter.api.extension.ExtendWith;
import org.mockito.Mock;
import org.mockito.junit.jupiter.MockitoExtension;

import java.util.ArrayList;
import java.util.List;

import static org.assertj.core.api.Assertions.assertThat;
import static org.mockito.ArgumentMatchers.any;
import static org.mockito.Mockito.mock;
import static org.mockito.Mockito.when;

@ExtendWith(MockitoExtension.class)
class GitHubRepositoryFileListingTest {

    @Mock
    private OkHttpClient httpClient;

    @Test
    void resolvesCommitToTreeAndWalksSubtreeShasAfterRecursiveTruncation()
            throws Exception {
        List<Request> requests = new ArrayList<>();
        when(httpClient.newCall(any(Request.class))).thenAnswer(invocation -> {
            Request request = invocation.getArgument(0);
            requests.add(request);
            String url = request.url().toString();
            String body;
            if (url.endsWith("/git/commits/commit-sha")) {
                body = "{\"tree\":{\"sha\":\"root-tree-sha\"}}";
            } else if (url.endsWith("/git/trees/root-tree-sha?recursive=1")) {
                body = """
                        {
                          "truncated": true,
                          "tree": [
                            {"path":"partial.txt","type":"blob","mode":"100644","sha":"partial"}
                          ]
                        }
                        """;
            } else if (url.endsWith("/git/trees/root-tree-sha")) {
                body = """
                        {
                          "tree": [
                            {"path":"README.md","type":"blob","mode":"100644","sha":"readme"},
                            {"path":"README-link","type":"blob","mode":"120000","sha":"link"},
                            {"path":"src","type":"tree","mode":"040000","sha":"src-tree-sha"},
                            {"path":"vendor","type":"commit","mode":"160000","sha":"submodule"}
                          ]
                        }
                        """;
            } else if (url.endsWith("/git/trees/src-tree-sha")) {
                body = """
                        {
                          "tree": [
                            {"path":"App.java","type":"blob","mode":"100755","sha":"app"}
                          ]
                        }
                        """;
            } else {
                throw new AssertionError("Unexpected GitHub request: " + url);
            }
            Response response = jsonResponse(request, body);
            Call call = mock(Call.class);
            when(call.execute()).thenReturn(response);
            return call;
        });

        List<String> files = new GitHubClient(httpClient)
                .listRepositoryFiles("owner", "repo", "commit-sha", 10);

        assertThat(files).containsExactly("README.md", "src/App.java");
        assertThat(requests).extracting(request -> request.url().toString())
                .containsExactly(
                        "https://api.github.com/repos/owner/repo/git/commits/commit-sha",
                        "https://api.github.com/repos/owner/repo/git/trees/root-tree-sha?recursive=1",
                        "https://api.github.com/repos/owner/repo/git/trees/root-tree-sha",
                        "https://api.github.com/repos/owner/repo/git/trees/src-tree-sha");
    }

    private static Response jsonResponse(Request request, String body) {
        return new Response.Builder()
                .request(request)
                .protocol(Protocol.HTTP_1_1)
                .code(200)
                .message("OK")
                .body(ResponseBody.create(
                        body, MediaType.parse("application/json")))
                .build();
    }
}
