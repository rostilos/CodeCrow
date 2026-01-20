package org.rostilos.codecrow.vcsclient.github.actions;

import com.fasterxml.jackson.databind.JsonNode;
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
class GetPullRequestActionTest {

    @Mock
    private OkHttpClient okHttpClient;

    @Mock
    private Call call;

    @Mock
    private Response response;

    @Mock
    private ResponseBody responseBody;

    private GetPullRequestAction action;

    @BeforeEach
    void setUp() {
        action = new GetPullRequestAction(okHttpClient);
    }

    @Test
    void testGetPullRequest_SuccessfulResponse_ReturnsJsonNode() throws IOException {
        String jsonResponse = """
            {
                "number": 123,
                "title": "Test PR",
                "state": "open",
                "head": {
                    "ref": "feature-branch",
                    "sha": "abc123"
                },
                "base": {
                    "ref": "main",
                    "sha": "def456"
                }
            }
            """;

        when(okHttpClient.newCall(any(Request.class))).thenReturn(call);
        when(call.execute()).thenReturn(response);
        when(response.isSuccessful()).thenReturn(true);
        when(response.body()).thenReturn(responseBody);
        when(responseBody.string()).thenReturn(jsonResponse);

        JsonNode result = action.getPullRequest("owner", "repo", 123);

        assertThat(result).isNotNull();
        assertThat(result.get("number").asInt()).isEqualTo(123);
        assertThat(result.get("title").asText()).isEqualTo("Test PR");
        verify(response).close();
    }

    @Test
    void testGetPullRequest_UnsuccessfulResponse_ThrowsIOException() throws IOException {
        when(okHttpClient.newCall(any(Request.class))).thenReturn(call);
        when(call.execute()).thenReturn(response);
        when(response.isSuccessful()).thenReturn(false);
        when(response.code()).thenReturn(404);
        when(response.body()).thenReturn(responseBody);
        when(responseBody.string()).thenReturn("Not found");

        assertThatThrownBy(() -> action.getPullRequest("owner", "repo", 123))
                .isInstanceOf(IOException.class)
                .hasMessageContaining("404")
                .hasMessageContaining("Not found");

        verify(response).close();
    }

    @Test
    void testGetPullRequest_IOException_PropagatesException() throws IOException {
        when(okHttpClient.newCall(any(Request.class))).thenReturn(call);
        when(call.execute()).thenThrow(new IOException("Network error"));

        assertThatThrownBy(() -> action.getPullRequest("owner", "repo", 123))
                .isInstanceOf(IOException.class)
                .hasMessage("Network error");
    }
}
