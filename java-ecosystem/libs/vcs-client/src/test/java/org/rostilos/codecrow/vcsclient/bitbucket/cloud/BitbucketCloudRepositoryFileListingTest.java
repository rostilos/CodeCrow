package org.rostilos.codecrow.vcsclient.bitbucket.cloud;

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
class BitbucketCloudRepositoryFileListingTest {

    @Mock
    private OkHttpClient httpClient;

    @Test
    void followsDirectoryAndPageListingsAndReturnsOnlyFiles() throws Exception {
        List<Request> requests = new ArrayList<>();
        when(httpClient.newCall(any(Request.class))).thenAnswer(invocation -> {
            Request request = invocation.getArgument(0);
            requests.add(request);
            String url = request.url().toString();
            String body;
            if (url.endsWith("/src/commit-sha/?pagelen=100")) {
                body = """
                        {
                          "values": [
                            {"path":"README.md","type":"commit_file","attributes":[]},
                            {"path":"README-link","type":"commit_file","attributes":["link"]},
                            {"path":"vendor","type":"commit_file","attributes":["subrepository"]},
                            {"path":"src folder","type":"commit_directory"},
                            {"path":"external","type":"commit"}
                          ],
                          "next":"https://api.bitbucket.org/2.0/repositories/workspace/repo/src/commit-sha/?pagelen=100&page=2"
                        }
                        """;
            } else if (url.endsWith("/src/commit-sha/?pagelen=100&page=2")) {
                body = """
                        {"values":[{"path":"LICENSE","type":"commit_file"}]}
                        """;
            } else if (url.endsWith("/src/commit-sha/src%20folder/?pagelen=100")) {
                body = """
                        {"values":[{"path":"src folder/App.java","type":"commit_file","attributes":["executable"]}]}
                        """;
            } else {
                throw new AssertionError("Unexpected Bitbucket request: " + url);
            }
            Response response = jsonResponse(request, body);
            Call call = mock(Call.class);
            when(call.execute()).thenReturn(response);
            return call;
        });

        List<String> files = new BitbucketCloudClient(httpClient)
                .listRepositoryFiles("workspace", "repo", "commit-sha", 10);

        assertThat(files).containsExactly(
                "LICENSE", "README.md", "src folder/App.java");
        assertThat(requests).extracting(request -> request.url().toString())
                .containsExactly(
                        "https://api.bitbucket.org/2.0/repositories/workspace/repo/src/commit-sha/?pagelen=100",
                        "https://api.bitbucket.org/2.0/repositories/workspace/repo/src/commit-sha/?pagelen=100&page=2",
                        "https://api.bitbucket.org/2.0/repositories/workspace/repo/src/commit-sha/src%20folder/?pagelen=100");
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
