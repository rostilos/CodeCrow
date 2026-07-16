package org.rostilos.codecrow.vcsclient.bitbucket.cloud.actions;

import okhttp3.Call;
import okhttp3.OkHttpClient;
import okhttp3.Request;
import okhttp3.Response;
import okhttp3.ResponseBody;
import org.junit.jupiter.api.BeforeEach;
import org.junit.jupiter.api.Test;
import org.junit.jupiter.api.extension.ExtendWith;
import org.mockito.ArgumentCaptor;
import org.mockito.Mock;
import org.mockito.junit.jupiter.MockitoExtension;

import java.io.IOException;

import static org.assertj.core.api.Assertions.assertThat;
import static org.assertj.core.api.Assertions.assertThatThrownBy;
import static org.mockito.ArgumentMatchers.any;
import static org.mockito.Mockito.when;

@ExtendWith(MockitoExtension.class)
class GetMergeBaseActionTest {
    private static final String BASE = "a".repeat(40);
    private static final String HEAD = "b".repeat(40);

    @Mock private OkHttpClient client;
    @Mock private Call call;
    @Mock private Response response;
    @Mock private ResponseBody body;

    private GetMergeBaseAction action;

    @BeforeEach
    void setUp() throws IOException {
        action = new GetMergeBaseAction(client);
        when(client.newCall(any())).thenReturn(call);
        when(call.execute()).thenReturn(response);
    }

    @Test
    void resolvesTheExactCommonAncestor() throws Exception {
        when(response.isSuccessful()).thenReturn(true);
        when(response.body()).thenReturn(body);
        when(body.string()).thenReturn("{\"hash\":\"" + "c".repeat(40) + "\"}");

        assertThat(action.getMergeBase("workspace", "repo", BASE, HEAD))
                .isEqualTo("c".repeat(40));

        ArgumentCaptor<Request> request = ArgumentCaptor.forClass(Request.class);
        org.mockito.Mockito.verify(client).newCall(request.capture());
        assertThat(request.getValue().url().toString())
                .contains("/merge-base/" + BASE + ".." + HEAD);
    }

    @Test
    void rejectsProviderFailureAndMissingHashes() throws Exception {
        when(response.isSuccessful()).thenReturn(false);
        when(response.code()).thenReturn(503);
        when(response.body()).thenReturn(body);
        when(body.string()).thenReturn("unavailable");
        assertThatThrownBy(() -> action.getMergeBase("workspace", "repo", BASE, HEAD))
                .isInstanceOf(IOException.class)
                .hasMessageContaining("503")
                .hasMessageContaining("unavailable");

        when(response.isSuccessful()).thenReturn(true);
        when(response.body()).thenReturn(body);
        when(body.string()).thenReturn("{}", "{\"hash\":\"\"}");
        assertThatThrownBy(() -> action.getMergeBase("workspace", "repo", BASE, HEAD))
                .isInstanceOf(IOException.class)
                .hasMessageContaining("omitted hash");
        assertThatThrownBy(() -> action.getMergeBase("workspace", "repo", BASE, HEAD))
                .isInstanceOf(IOException.class)
                .hasMessageContaining("omitted hash");
    }

    @Test
    void rejectsNullBodiesInvalidRevisionsAndNullClient() throws Exception {
        when(response.isSuccessful()).thenReturn(false, true);
        when(response.code()).thenReturn(500);
        when(response.body()).thenReturn(null);
        assertThatThrownBy(() -> action.getMergeBase("workspace", "repo", BASE, HEAD))
                .isInstanceOf(IOException.class)
                .hasMessageContaining("500");
        assertThatThrownBy(() -> action.getMergeBase("workspace", "repo", BASE, HEAD))
                .isInstanceOf(IOException.class)
                .hasMessageContaining("omitted hash");

        assertThatThrownBy(() -> action.getMergeBase("workspace", "repo", "main", HEAD))
                .isInstanceOf(IllegalArgumentException.class)
                .hasMessageContaining("baseCommit");
        assertThatThrownBy(() -> action.getMergeBase("workspace", "repo", BASE, "HEAD"))
                .isInstanceOf(IllegalArgumentException.class)
                .hasMessageContaining("headCommit");
        assertThatThrownBy(() -> new GetMergeBaseAction(null))
                .isInstanceOf(NullPointerException.class);
    }
}
