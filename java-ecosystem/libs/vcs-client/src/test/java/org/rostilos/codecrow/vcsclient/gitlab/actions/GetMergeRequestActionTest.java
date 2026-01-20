package org.rostilos.codecrow.vcsclient.gitlab.actions;

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
class GetMergeRequestActionTest {

    @Mock
    private OkHttpClient okHttpClient;

    @Mock
    private Call call;

    @Mock
    private Response response;

    @Mock
    private ResponseBody responseBody;

    private GetMergeRequestAction action;

    @BeforeEach
    void setUp() {
        action = new GetMergeRequestAction(okHttpClient);
    }

    @Test
    void testGetMergeRequest_SuccessfulResponse_ReturnsJsonNode() throws IOException {
        String jsonResponse = """
            {
                "iid": 123,
                "title": "Test MR",
                "state": "opened",
                "source_branch": "feature",
                "target_branch": "main"
            }
            """;

        when(okHttpClient.newCall(any(Request.class))).thenReturn(call);
        when(call.execute()).thenReturn(response);
        when(response.isSuccessful()).thenReturn(true);
        when(response.body()).thenReturn(responseBody);
        when(responseBody.string()).thenReturn(jsonResponse);

        JsonNode result = action.getMergeRequest("namespace", "project", 123);

        assertThat(result).isNotNull();
        assertThat(result.get("iid").asInt()).isEqualTo(123);
        assertThat(result.get("title").asText()).isEqualTo("Test MR");
        verify(response).close();
    }

    @Test
    void testGetMergeRequest_UnsuccessfulResponse_ThrowsIOException() throws IOException {
        when(okHttpClient.newCall(any(Request.class))).thenReturn(call);
        when(call.execute()).thenReturn(response);
        when(response.isSuccessful()).thenReturn(false);
        when(response.code()).thenReturn(404);
        when(response.body()).thenReturn(responseBody);
        when(responseBody.string()).thenReturn("Not found");

        assertThatThrownBy(() -> action.getMergeRequest("namespace", "project", 123))
                .isInstanceOf(IOException.class)
                .hasMessageContaining("404");

        verify(response).close();
    }
}
