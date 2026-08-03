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
import org.rostilos.codecrow.vcsclient.model.VcsPullRequestComment;

import java.util.List;

import static org.assertj.core.api.Assertions.assertThat;
import static org.mockito.ArgumentMatchers.any;
import static org.mockito.Mockito.mock;
import static org.mockito.Mockito.when;

@ExtendWith(MockitoExtension.class)
class BitbucketCloudClientCommentThreadTest {

    @Mock
    private OkHttpClient httpClient;

    @Test
    void loadsTheRootAndRepliesForTheTriggeringComment() throws Exception {
        when(httpClient.newCall(any(Request.class))).thenAnswer(invocation -> {
            Request request = invocation.getArgument(0);
            Response response = new Response.Builder()
                    .request(request)
                    .protocol(Protocol.HTTP_1_1)
                    .code(200)
                    .message("OK")
                    .body(ResponseBody.create("""
                            {
                              "values": [
                                {"id":100,"created_on":"2026-08-01T10:00:00Z","content":{"raw":"CodeCrow finding"},"user":{"nickname":"codecrowai"}},
                                {"id":101,"created_on":"2026-08-01T10:01:00Z","parent":{"id":100},"content":{"raw":"Can you explain?"},"user":{"nickname":"reviewer"}},
                                {"id":102,"created_on":"2026-08-01T10:02:00Z","parent":{"id":101},"content":{"raw":"Earlier answer"},"user":{"nickname":"codecrowai"}},
                                {"id":200,"created_on":"2026-08-01T10:03:00Z","content":{"raw":"Other thread"},"user":{"nickname":"someone"}}
                              ]
                            }
                            """, MediaType.parse("application/json")))
                    .build();
            Call call = mock(Call.class);
            when(call.execute()).thenReturn(response);
            return call;
        });

        List<VcsPullRequestComment> comments = new BitbucketCloudClient(httpClient)
                .getPullRequestCommentThread("workspace", "repo", 7L, "102", "101", true);

        assertThat(comments).extracting(VcsPullRequestComment::id)
                .containsExactly("100", "101", "102");
        assertThat(comments).extracting(VcsPullRequestComment::threadId)
                .containsOnly("100");
    }
}
