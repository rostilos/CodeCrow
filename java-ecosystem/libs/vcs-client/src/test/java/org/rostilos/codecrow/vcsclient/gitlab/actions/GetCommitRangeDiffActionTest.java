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
class GetCommitRangeDiffActionTest {

    @Mock
    private OkHttpClient okHttpClient;

    @Mock
    private Call call;

    @Mock
    private Response response;

    @Mock
    private ResponseBody responseBody;

    private GetCommitRangeDiffAction action;

    @BeforeEach
    void setUp() {
        action = new GetCommitRangeDiffAction(okHttpClient);
    }

    @Test
    void testGetCommitRangeDiff_SuccessfulResponse_ReturnsDiff() throws IOException {
        String jsonResponse = """
            {
                "diffs": [
                    {
                        "diff": "diff --git a/file.java b/file.java\\n+new line",
                        "new_path": "file.java",
                        "old_path": "file.java"
                    }
                ]
            }
            """;

        when(okHttpClient.newCall(any(Request.class))).thenReturn(call);
        when(call.execute()).thenReturn(response);
        when(response.isSuccessful()).thenReturn(true);
        when(response.body()).thenReturn(responseBody);
        when(responseBody.string()).thenReturn(jsonResponse);

        String result = action.getCommitRangeDiff("namespace", "project", "abc123", "def456");

        assertThat(result).contains("diff --git");
        verify(okHttpClient).newCall(argThat(request ->
                request.url().toString().contains("from=abc123") &&
                request.url().toString().contains("to=def456")
        ));
        verify(response).close();
    }

    @Test
    void testGetCommitRangeDiff_UnsuccessfulResponse_ThrowsIOException() throws IOException {
        when(okHttpClient.newCall(any(Request.class))).thenReturn(call);
        when(call.execute()).thenReturn(response);
        when(response.isSuccessful()).thenReturn(false);
        when(response.code()).thenReturn(404);
        when(response.body()).thenReturn(responseBody);
        when(responseBody.string()).thenReturn("Not found");

        assertThatThrownBy(() -> action.getCommitRangeDiff("namespace", "project", "invalid1", "invalid2"))
                .isInstanceOf(IOException.class)
                .hasMessageContaining("404");

        verify(response).close();
    }
}
