package org.rostilos.codecrow.vcsclient.bitbucket.cloud.actions;

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
class GetPullRequestDiffActionTest {

    @Mock
    private OkHttpClient okHttpClient;

    @Mock
    private Call call;

    @Mock
    private Response response;

    @Mock
    private ResponseBody responseBody;

    private GetPullRequestDiffAction action;

    @BeforeEach
    void setUp() {
        action = new GetPullRequestDiffAction(okHttpClient);
    }

    @Test
    void testGetPullRequestDiff_SuccessfulResponse_ReturnsDiff() throws IOException {
        String expectedDiff = "diff --git a/file.java b/file.java\n+new line";

        when(okHttpClient.newCall(any(Request.class))).thenReturn(call);
        when(call.execute()).thenReturn(response);
        when(response.isSuccessful()).thenReturn(true);
        when(response.body()).thenReturn(responseBody);
        when(responseBody.string()).thenReturn(expectedDiff);

        String result = action.getPullRequestDiff("workspace", "repo", "123");

        assertThat(result).isEqualTo(expectedDiff);
        verify(okHttpClient).newCall(argThat(request ->
                request.url().toString().contains("pullrequests/123/diff")
        ));
        verify(response).close();
    }

    @Test
    void testGetPullRequestDiff_UnsuccessfulResponse_ThrowsIOException() throws IOException {
        when(okHttpClient.newCall(any(Request.class))).thenReturn(call);
        when(call.execute()).thenReturn(response);
        when(response.isSuccessful()).thenReturn(false);
        when(response.code()).thenReturn(404);
        when(response.body()).thenReturn(responseBody);
        when(responseBody.string()).thenReturn("Not found");

        assertThatThrownBy(() -> action.getPullRequestDiff("workspace", "repo", "123"))
                .isInstanceOf(IOException.class)
                .hasMessageContaining("404");

        verify(response).close();
    }

    @Test
    void testGetPullRequestDiff_HandlesNullWorkspace() throws IOException {
        when(okHttpClient.newCall(any(Request.class))).thenReturn(call);
        when(call.execute()).thenReturn(response);
        when(response.isSuccessful()).thenReturn(true);
        when(response.body()).thenReturn(responseBody);
        when(responseBody.string()).thenReturn("diff content");

        action.getPullRequestDiff(null, "repo", "123");

        verify(okHttpClient).newCall(argThat(request ->
                request.url().toString().contains("/repositories//repo/pullrequests/")
        ));
    }
}
