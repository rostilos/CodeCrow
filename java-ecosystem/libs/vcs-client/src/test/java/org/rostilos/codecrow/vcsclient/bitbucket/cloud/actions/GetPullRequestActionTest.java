package org.rostilos.codecrow.vcsclient.bitbucket.cloud.actions;

import okhttp3.Call;
import okhttp3.OkHttpClient;
import okhttp3.Request;
import okhttp3.Response;
import okhttp3.ResponseBody;
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
                    },
                    "commit": {
                        "hash": "bbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbb"
                    }
                },
                "destination": {
                    "branch": {
                        "name": "main"
                    },
                    "commit": {
                        "hash": "aaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaa"
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
        assertThat(result.getSourceCommit())
                .isEqualTo("bbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbb");
        assertThat(result.getDestinationCommit())
                .isEqualTo("aaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaa");
        verify(response).close();
    }

    @Test
    void testGetPullRequest_MissingDestinationDoesNotInventAComparisonCommit() throws IOException {
        when(okHttpClient.newCall(any(Request.class))).thenReturn(call);
        when(call.execute()).thenReturn(response);
        when(response.isSuccessful()).thenReturn(true);
        when(response.body()).thenReturn(responseBody);
        when(responseBody.string()).thenReturn("{\"title\":\"No destination\"}");

        GetPullRequestAction.PullRequestMetadata result = action.getPullRequest(
                "workspace", "repo", "123");

        assertThat(result.getDestinationCommit()).isNull();
    }

    @Test
    void testGetPullRequest_DestinationWithoutCommitDoesNotInventAComparisonCommit() throws IOException {
        when(okHttpClient.newCall(any(Request.class))).thenReturn(call);
        when(call.execute()).thenReturn(response);
        when(response.isSuccessful()).thenReturn(true);
        when(response.body()).thenReturn(responseBody);
        when(responseBody.string()).thenReturn(
                "{\"title\":\"No commit\",\"destination\":{\"branch\":{\"name\":\"main\"}}}");

        GetPullRequestAction.PullRequestMetadata result = action.getPullRequest(
                "workspace", "repo", "123");

        assertThat(result.getDestinationCommit()).isNull();
    }

    @Test
    void legacyMetadataConstructorRemainsWireCompatible() {
        GetPullRequestAction.PullRequestMetadata metadata =
                new GetPullRequestAction.PullRequestMetadata(
                        "title", "description", "OPEN", "feature", "main");

        assertThat(metadata.getDestinationCommit()).isNull();
        assertThat(metadata.getSourceCommit()).isNull();
    }

    @Test
    void destinationCommitConstructorAndReferenceAccessorsRemainWireCompatible() {
        GetPullRequestAction.PullRequestMetadata metadata =
                new GetPullRequestAction.PullRequestMetadata(
                        "title", "description", "OPEN", "feature", "main", "base-sha");

        assertThat(metadata.getSourceRef()).isEqualTo("feature");
        assertThat(metadata.getDestRef()).isEqualTo("main");
        assertThat(metadata.getSourceCommit()).isNull();
        assertThat(metadata.getDestinationCommit()).isEqualTo("base-sha");
    }

    @Test
    void successfulResponseWithoutBodyUsesEmptyMetadata() throws IOException {
        when(okHttpClient.newCall(any(Request.class))).thenReturn(call);
        when(call.execute()).thenReturn(response);
        when(response.isSuccessful()).thenReturn(true);
        when(response.body()).thenReturn(null);

        GetPullRequestAction.PullRequestMetadata result = action.getPullRequest(
                "workspace", "repo", "123");

        assertThat(result.getTitle()).isEmpty();
        assertThat(result.getDescription()).isEmpty();
        assertThat(result.getState()).isEmpty();
        assertThat(result.getSourceRef()).isEmpty();
        assertThat(result.getDestRef()).isEmpty();
        assertThat(result.getSourceCommit()).isNull();
        assertThat(result.getDestinationCommit()).isNull();
    }

    @Test
    void sourceWithoutCommitKeepsExactHeadUnset() throws IOException {
        when(okHttpClient.newCall(any(Request.class))).thenReturn(call);
        when(call.execute()).thenReturn(response);
        when(response.isSuccessful()).thenReturn(true);
        when(response.body()).thenReturn(responseBody);
        when(responseBody.string()).thenReturn(
                "{\"source\":{\"branch\":{\"name\":\"feature\"}}}");

        GetPullRequestAction.PullRequestMetadata result = action.getPullRequest(
                "workspace", "repo", "123");

        assertThat(result.getSourceRef()).isEqualTo("feature");
        assertThat(result.getSourceCommit()).isNull();
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

    @Test
    void unsuccessfulResponseWithoutBodyStillReportsTheFailure() throws IOException {
        when(okHttpClient.newCall(any(Request.class))).thenReturn(call);
        when(call.execute()).thenReturn(response);
        when(response.isSuccessful()).thenReturn(false);
        when(response.code()).thenReturn(500);
        when(response.body()).thenReturn(null);

        assertThatThrownBy(() -> action.getPullRequest("workspace", "repo", "123"))
                .isInstanceOf(IOException.class)
                .hasMessageContaining("500");
    }
}
