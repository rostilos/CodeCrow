package org.rostilos.codecrow.vcsclient.github.actions;

import okhttp3.*;
import org.junit.jupiter.api.BeforeEach;
import org.junit.jupiter.api.Test;
import org.junit.jupiter.api.extension.ExtendWith;
import org.mockito.Mock;
import org.mockito.junit.jupiter.MockitoExtension;

import java.io.IOException;

import static org.assertj.core.api.Assertions.assertThat;
import static org.assertj.core.api.Assertions.assertThatThrownBy;
import static org.mockito.ArgumentMatchers.any;
import static org.mockito.Mockito.*;

@ExtendWith(MockitoExtension.class)
class CommentOnPullRequestActionTest {

    @Mock
    private OkHttpClient okHttpClient;

    @Mock
    private Call call;

    @Mock
    private Response response;

    @Mock
    private ResponseBody responseBody;

    private CommentOnPullRequestAction action;

    @BeforeEach
    void setUp() {
        action = new CommentOnPullRequestAction(okHttpClient);
    }

    @Test
    void testPostComment_SuccessfulResponse_NoException() throws IOException {
        when(okHttpClient.newCall(any(Request.class))).thenReturn(call);
        when(call.execute()).thenReturn(response);
        when(response.isSuccessful()).thenReturn(true);

        action.postComment("owner", "repo", 123, "Test comment");

        verify(okHttpClient).newCall(any(Request.class));
        verify(response).close();
    }

    @Test
    void testPostComment_UnsuccessfulResponse_ThrowsIOException() throws IOException {
        when(okHttpClient.newCall(any(Request.class))).thenReturn(call);
        when(call.execute()).thenReturn(response);
        when(response.isSuccessful()).thenReturn(false);
        when(response.code()).thenReturn(403);
        when(response.body()).thenReturn(responseBody);
        when(responseBody.string()).thenReturn("Forbidden");

        assertThatThrownBy(() -> action.postComment("owner", "repo", 123, "Test comment"))
                .isInstanceOf(IOException.class)
                .hasMessageContaining("403")
                .hasMessageContaining("Forbidden");

        verify(response).close();
    }

    @Test
    void testPostComment_IOException_PropagatesException() throws IOException {
        when(okHttpClient.newCall(any(Request.class))).thenReturn(call);
        when(call.execute()).thenThrow(new IOException("Network error"));

        assertThatThrownBy(() -> action.postComment("owner", "repo", 123, "Test comment"))
                .isInstanceOf(IOException.class)
                .hasMessage("Network error");
    }

    @Test
    void testPostReviewComment_SuccessfulResponse_NoException() throws IOException {
        when(okHttpClient.newCall(any(Request.class))).thenReturn(call);
        when(call.execute()).thenReturn(response);
        when(response.isSuccessful()).thenReturn(true);

        action.postReviewComment("owner", "repo", 123, "Review comment", "abc123", "src/file.java", 10);

        verify(okHttpClient).newCall(any(Request.class));
        verify(response).close();
    }
}
