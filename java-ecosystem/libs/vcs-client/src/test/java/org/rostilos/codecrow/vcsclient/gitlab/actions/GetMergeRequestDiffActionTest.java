package org.rostilos.codecrow.vcsclient.gitlab.actions;

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
class GetMergeRequestDiffActionTest {

    @Mock
    private OkHttpClient okHttpClient;

    @Mock
    private Call call;

    @Mock
    private Response response;

    @Mock
    private ResponseBody responseBody;

    private GetMergeRequestDiffAction action;

    @BeforeEach
    void setUp() {
        action = new GetMergeRequestDiffAction(okHttpClient);
    }

    @Test
    void testGetMergeRequestDiff_SuccessfulResponse_ReturnsDiff() throws IOException {
        String jsonResponse = """
            [
                {
                    "diff": "diff --git a/file.java b/file.java\\n+new line",
                    "new_path": "file.java",
                    "old_path": "file.java"
                }
            ]
            """;

        when(okHttpClient.newCall(any(Request.class))).thenReturn(call);
        when(call.execute()).thenReturn(response);
        when(response.isSuccessful()).thenReturn(true);
        when(response.body()).thenReturn(responseBody);
        when(responseBody.string()).thenReturn(jsonResponse);
        when(response.header("X-Total-Pages")).thenReturn("1");

        String result = action.getMergeRequestDiff("namespace", "project", 123);

        assertThat(result).contains("diff --git");
        verify(response).close();
    }

    @Test
    void testGetMergeRequestDiff_UnsuccessfulResponse_ThrowsIOException() throws IOException {
        when(okHttpClient.newCall(any(Request.class))).thenReturn(call);
        when(call.execute()).thenReturn(response);
        when(response.isSuccessful()).thenReturn(false);
        when(response.code()).thenReturn(404);
        when(response.body()).thenReturn(responseBody);
        when(responseBody.string()).thenReturn("Not found");

        assertThatThrownBy(() -> action.getMergeRequestDiff("namespace", "project", 123))
                .isInstanceOf(IOException.class)
                .hasMessageContaining("404");

        verify(response).close();
    }
}
