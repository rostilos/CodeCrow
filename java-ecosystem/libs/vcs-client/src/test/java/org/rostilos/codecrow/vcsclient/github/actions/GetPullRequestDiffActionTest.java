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
        when(response.code()).thenReturn(200);
        when(response.body()).thenReturn(responseBody);
        when(responseBody.string()).thenReturn(expectedDiff);

        String result = action.getPullRequestDiff("owner", "repo", 123);

        assertThat(result).isEqualTo(expectedDiff);
        verify(response).close();
    }

    @Test
    void testGetPullRequestDiff_406Response_FallsBackToFiles() throws IOException {
        String filesJson = """
            [
                {
                    "filename": "file.java",
                    "patch": "diff content"
                }
            ]
            """;

        Response diffResponse = mock(Response.class);
        when(diffResponse.code()).thenReturn(406);
        doNothing().when(diffResponse).close();

        Response filesResponse = mock(Response.class);
        ResponseBody filesBody = mock(ResponseBody.class);
        when(filesResponse.isSuccessful()).thenReturn(true);
        when(filesResponse.body()).thenReturn(filesBody);
        when(filesBody.string()).thenReturn(filesJson);
        when(filesResponse.header("Link")).thenReturn(null);

        when(okHttpClient.newCall(any(Request.class))).thenReturn(call);
        when(call.execute())
                .thenReturn(diffResponse)
                .thenReturn(filesResponse);

        String result = action.getPullRequestDiff("owner", "repo", 123);

        assertThat(result).contains("diff content");
        verify(diffResponse).close();
        verify(filesResponse).close();
    }

    @Test
    void testGetPullRequestDiff_UnsuccessfulResponse_ThrowsIOException() throws IOException {
        when(okHttpClient.newCall(any(Request.class))).thenReturn(call);
        when(call.execute()).thenReturn(response);
        when(response.isSuccessful()).thenReturn(false);
        when(response.code()).thenReturn(404);
        when(response.body()).thenReturn(responseBody);
        when(responseBody.string()).thenReturn("Not found");

        assertThatThrownBy(() -> action.getPullRequestDiff("owner", "repo", 123))
                .isInstanceOf(IOException.class)
                .hasMessageContaining("404");

        verify(response).close();
    }
}
