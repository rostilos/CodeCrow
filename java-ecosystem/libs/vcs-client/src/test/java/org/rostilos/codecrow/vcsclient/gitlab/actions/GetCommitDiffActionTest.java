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
class GetCommitDiffActionTest {

    @Mock
    private OkHttpClient okHttpClient;

    @Mock
    private Call call;

    @Mock
    private Response response;

    @Mock
    private ResponseBody responseBody;

    private GetCommitDiffAction action;

    @BeforeEach
    void setUp() {
        action = new GetCommitDiffAction(okHttpClient);
    }

    @Test
    void testGetCommitDiff_SuccessfulResponse_ReturnsDiff() throws IOException {
        String jsonResponse = """
            [
                {
                    "diff": "diff --git a/file.java b/file.java\\n+new line",
                    "new_path": "file.java",
                    "old_path": "file.java",
                    "new_file": false,
                    "renamed_file": false,
                    "deleted_file": false
                }
            ]
            """;
        
        when(okHttpClient.newCall(any(Request.class))).thenReturn(call);
        when(call.execute()).thenReturn(response);
        when(response.isSuccessful()).thenReturn(true);
        when(response.body()).thenReturn(responseBody);
        when(responseBody.string()).thenReturn(jsonResponse);

        String result = action.getCommitDiff("namespace", "project", "abc123");

        assertThat(result).isNotEmpty();
        assertThat(result).contains("diff --git");
        verify(response).close();
    }

    @Test
    void testGetCommitDiff_EmptyDiffArray_ReturnsEmptyString() throws IOException {
        when(okHttpClient.newCall(any(Request.class))).thenReturn(call);
        when(call.execute()).thenReturn(response);
        when(response.isSuccessful()).thenReturn(true);
        when(response.body()).thenReturn(responseBody);
        when(responseBody.string()).thenReturn("[]");

        String result = action.getCommitDiff("namespace", "project", "abc123");

        assertThat(result).isEmpty();
        verify(response).close();
    }

    @Test
    void testGetCommitDiff_NullBody_ReturnsEmptyString() throws IOException {
        when(okHttpClient.newCall(any(Request.class))).thenReturn(call);
        when(call.execute()).thenReturn(response);
        when(response.isSuccessful()).thenReturn(true);
        when(response.body()).thenReturn(null);

        String result = action.getCommitDiff("namespace", "project", "abc123");

        assertThat(result).isEmpty();
        verify(response).close();
    }

    @Test
    void testGetCommitDiff_UnsuccessfulResponse_ThrowsIOException() throws IOException {
        when(okHttpClient.newCall(any(Request.class))).thenReturn(call);
        when(call.execute()).thenReturn(response);
        when(response.isSuccessful()).thenReturn(false);
        when(response.code()).thenReturn(404);
        when(response.body()).thenReturn(responseBody);
        when(responseBody.string()).thenReturn("Not found");

        assertThatThrownBy(() -> action.getCommitDiff("namespace", "project", "invalid"))
                .isInstanceOf(IOException.class)
                .hasMessageContaining("404")
                .hasMessageContaining("Not found");

        verify(response).close();
    }

    @Test
    void testGetCommitDiff_IOException_PropagatesException() throws IOException {
        when(okHttpClient.newCall(any(Request.class))).thenReturn(call);
        when(call.execute()).thenThrow(new IOException("Network timeout"));

        assertThatThrownBy(() -> action.getCommitDiff("namespace", "project", "abc123"))
                .isInstanceOf(IOException.class)
                .hasMessage("Network timeout");
    }

    @Test
    void testGetCommitDiff_EncodesProjectPath() throws IOException {
        when(okHttpClient.newCall(any(Request.class))).thenReturn(call);
        when(call.execute()).thenReturn(response);
        when(response.isSuccessful()).thenReturn(true);
        when(response.body()).thenReturn(responseBody);
        when(responseBody.string()).thenReturn("[]");

        action.getCommitDiff("my-group", "my project", "abc123");

        verify(okHttpClient).newCall(argThat(request -> 
            request.url().toString().contains("my-group%2Fmy+project") ||
            request.url().toString().contains("my-group%2Fmy%20project")
        ));
        verify(response).close();
    }
}
