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
import org.rostilos.codecrow.vcsclient.model.VcsPullRequestComment;

import java.util.List;

import static org.assertj.core.api.Assertions.assertThat;
import static org.mockito.ArgumentMatchers.any;
import static org.mockito.Mockito.mock;
import static org.mockito.Mockito.when;

@ExtendWith(MockitoExtension.class)
class GitHubClientCommentThreadTest {

    @Mock
    private OkHttpClient httpClient;

    @Test
    void loadsOnlyTheTriggeringReviewThread() throws Exception {
        when(httpClient.newCall(any(Request.class))).thenAnswer(invocation -> {
            Request request = invocation.getArgument(0);
            Response response = new Response.Builder()
                    .request(request)
                    .protocol(Protocol.HTTP_1_1)
                    .code(200)
                    .message("OK")
                    .body(ResponseBody.create("""
                            [
                              {"id":10,"body":"CodeCrow finding","created_at":"2026-08-01T10:00:00Z","user":{"login":"codecrow-bot"}},
                              {"id":11,"in_reply_to_id":10,"body":"Why?","created_at":"2026-08-01T10:01:00Z","user":{"login":"reviewer"}},
                              {"id":20,"body":"Other thread","created_at":"2026-08-01T10:02:00Z","user":{"login":"someone"}}
                            ]
                            """, MediaType.parse("application/json")))
                    .build();
            Call call = mock(Call.class);
            when(call.execute()).thenReturn(response);
            return call;
        });

        List<VcsPullRequestComment> comments = new GitHubClient(httpClient)
                .getPullRequestCommentThread("owner", "repo", 7L, "11", "10", true);

        assertThat(comments).extracting(VcsPullRequestComment::id)
                .containsExactly("10", "11");
        assertThat(comments.get(0).authorUsername()).isEqualTo("codecrow-bot");
        assertThat(comments.get(1).parentId()).isEqualTo("10");
    }
}
