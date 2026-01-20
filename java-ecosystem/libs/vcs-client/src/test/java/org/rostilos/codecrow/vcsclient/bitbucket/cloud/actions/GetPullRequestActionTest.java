package org.rostilos.codecrow.vcsclient.bitbucket.cloud.actions;

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
    void testGetPullRequest_SuccessfulResponse_ReturnsMetadata() throws IOException {
        String jsonResponse = """
            {
                "title": "Test PR",
                "description": "Test description",
                "state": "OPEN",
                "source": {
                    "branch": {
                        "name": "feature"
                    }
                },
                "destination": {
                    "branch": {
                        "name": "main"
                    }
                }
            }
            """;

        when(okHttpClient.newCall(any(Request.class))).thenReturn(call);
        when(call.execute()).thenReturn(response);
        when(response.isSuccessful()).thenReturn(true);
        when(response.body()).thenReturn(responseBody);
        when(responseBody.string()).thenReturn(jsonResponse);

        GetPullRequestAction.PullRequestMetadata result = action.getPullRequest("workspace", "repo", "123");

        assertThat(result).isNotNull();
        assertThat(result.getTitle()).isEqualTo("Test PR");
        assertThat(result.getState()).isEqualTo("OPEN");
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

        assertThatThrownBy(() -> action.getPullRequest("workspace", "repo", "123"))
                .isInstanceOf(IOException.class)
                .hasMessageContaining("404");

        verify(response).close();
    }
}
