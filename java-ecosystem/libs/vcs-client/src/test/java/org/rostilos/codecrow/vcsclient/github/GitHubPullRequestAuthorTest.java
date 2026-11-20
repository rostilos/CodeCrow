package org.rostilos.codecrow.vcsclient.github;

import okhttp3.*;
import org.junit.jupiter.api.Test;
import static org.assertj.core.api.Assertions.*;
import static org.mockito.Mockito.*;

class GitHubPullRequestAuthorTest {
    @Test void authorIdentityIsStableAndMissingAuthorsStayUnknown() throws Exception {
        OkHttpClient http = mock(OkHttpClient.class);
        for (String author : new String[]{"{\"id\":42,\"login\":\"developer\"}", "null"}) {
            when(http.newCall(any(Request.class))).thenAnswer(invocation -> {
                Request request = invocation.getArgument(0);
                Call call = mock(Call.class);
                when(call.execute()).thenReturn(new Response.Builder().request(request).code(200).message("OK")
                        .protocol(Protocol.HTTP_1_1).body(ResponseBody.create(
                                "{\"number\":1,\"user\":" + author + ",\"head\":{},\"base\":{}}", MediaType.get("application/json"))).build());
                return call;
            });
            var pr = new GitHubClient(http).getPullRequest("owner","repository",1);
            assertThat(pr.authorId()).isEqualTo(author.equals("null") ? null : "42");
            assertThat(pr.authorName()).isEqualTo(author.equals("null") ? null : "developer");
        }
    }
}
