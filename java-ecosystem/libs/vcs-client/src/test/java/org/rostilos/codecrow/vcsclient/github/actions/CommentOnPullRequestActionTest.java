package org.rostilos.codecrow.vcsclient.github.actions;

import com.fasterxml.jackson.databind.JsonNode;
import com.fasterxml.jackson.databind.ObjectMapper;
import okhttp3.*;
import okio.Buffer;
import org.junit.jupiter.api.BeforeEach;
import org.junit.jupiter.api.Test;
import org.junit.jupiter.api.extension.ExtendWith;
import org.mockito.ArgumentCaptor;
import org.mockito.Mock;
import org.mockito.junit.jupiter.MockitoExtension;

import java.io.IOException;
import java.util.ArrayList;
import java.util.List;
import java.util.Map;

import static org.assertj.core.api.Assertions.assertThat;
import static org.assertj.core.api.Assertions.assertThatThrownBy;
import static org.mockito.ArgumentMatchers.any;
import static org.mockito.Mockito.*;

@ExtendWith(MockitoExtension.class)
class CommentOnPullRequestActionTest {

    @Mock
    private OkHttpClient okHttpClient;

    @Mock
    private Call call;

    @Mock
    private Response response;

    @Mock
    private ResponseBody responseBody;

    private CommentOnPullRequestAction action;

    @BeforeEach
    void setUp() {
        action = new CommentOnPullRequestAction(okHttpClient);
    }

    @Test
    void testPostComment_SuccessfulResponse_NoException() throws IOException {
        when(okHttpClient.newCall(any(Request.class))).thenReturn(call);
        when(call.execute()).thenReturn(response);
        when(response.isSuccessful()).thenReturn(true);

        action.postComment("owner", "repo", 123, "Test comment");

        verify(okHttpClient).newCall(any(Request.class));
        verify(response).close();
    }

    @Test
    void testPostComment_UnsuccessfulResponse_ThrowsIOException() throws IOException {
        when(okHttpClient.newCall(any(Request.class))).thenReturn(call);
        when(call.execute()).thenReturn(response);
        when(response.isSuccessful()).thenReturn(false);
        when(response.code()).thenReturn(403);
        when(response.body()).thenReturn(responseBody);
        when(responseBody.string()).thenReturn("Forbidden");

        assertThatThrownBy(() -> action.postComment("owner", "repo", 123, "Test comment"))
                .isInstanceOf(IOException.class)
                .hasMessageContaining("403")
                .hasMessageContaining("Forbidden");

        verify(response).close();
    }

    @Test
    void testPostComment_IOException_PropagatesException() throws IOException {
        when(okHttpClient.newCall(any(Request.class))).thenReturn(call);
        when(call.execute()).thenThrow(new IOException("Network error"));

        assertThatThrownBy(() -> action.postComment("owner", "repo", 123, "Test comment"))
                .isInstanceOf(IOException.class)
                .hasMessage("Network error");
    }

    @Test
    void testPostReviewComment_SuccessfulResponse_NoException() throws IOException {
        when(okHttpClient.newCall(any(Request.class))).thenReturn(call);
        when(call.execute()).thenReturn(response);
        when(response.isSuccessful()).thenReturn(true);

        action.postReviewComment("owner", "repo", 123, "Review comment", "abc123", "src/file.java", 10);

        verify(okHttpClient).newCall(any(Request.class));
        verify(response).close();
    }

    @Test
    void postReviewCommentReplyTargetsTopLevelThreadComment() throws IOException {
        List<Request> requests = new ArrayList<>();
        when(okHttpClient.newCall(any(Request.class))).thenAnswer(invocation -> {
            Request request = invocation.getArgument(0);
            requests.add(request);
            Response requestResponse = new Response.Builder()
                    .request(request)
                    .protocol(Protocol.HTTP_1_1)
                    .code(201)
                    .message("Created")
                    .body(ResponseBody.create(
                            "{\"id\":987}", MediaType.parse("application/json")))
                    .build();
            Call requestCall = mock(Call.class);
            when(requestCall.execute()).thenReturn(requestResponse);
            return requestCall;
        });

        String replyId = action.postReviewCommentReply(
                "owner", "repo", 123, 456L, "Thread-aware answer");

        assertThat(replyId).isEqualTo("987");
        assertThat(requests).hasSize(1);
        Request request = requests.get(0);
        assertThat(request.method()).isEqualTo("POST");
        assertThat(request.url().encodedPath())
                .isEqualTo("/repos/owner/repo/pulls/123/comments/456/replies");
        Buffer body = new Buffer();
        request.body().writeTo(body);
        assertThat(new ObjectMapper().readTree(body.readUtf8()).path("body").asText())
                .isEqualTo("Thread-aware answer");
    }

    @Test
    void createPullRequestReview_SubmitsGroupedInlineComments() throws IOException {
        when(okHttpClient.newCall(any(Request.class))).thenReturn(call);
        when(call.execute()).thenReturn(response);
        when(response.isSuccessful()).thenReturn(true);
        when(response.body()).thenReturn(ResponseBody.create(
                "{\"id\":456}", MediaType.parse("application/json")));

        String reviewId = action.createPullRequestReview(
                "owner",
                "repo",
                123,
                "abc123",
                "CodeCrow review",
                "COMMENT",
                List.of(Map.of(
                        "path", "src/file.java",
                        "line", 10,
                        "side", "RIGHT",
                        "body", "Inline finding"
                ))
        );

        ArgumentCaptor<Request> requestCaptor = ArgumentCaptor.forClass(Request.class);
        verify(okHttpClient).newCall(requestCaptor.capture());
        Request request = requestCaptor.getValue();
        assertThat(request.method()).isEqualTo("POST");
        assertThat(request.url().encodedPath()).isEqualTo("/repos/owner/repo/pulls/123/reviews");

        Buffer buffer = new Buffer();
        request.body().writeTo(buffer);
        JsonNode payload = new ObjectMapper().readTree(buffer.readUtf8());
        assertThat(payload.path("commit_id").asText()).isEqualTo("abc123");
        assertThat(payload.path("event").asText()).isEqualTo("COMMENT");
        assertThat(payload.path("comments").size()).isEqualTo(1);
        assertThat(payload.path("comments").get(0).path("path").asText())
                .isEqualTo("src/file.java");
        assertThat(payload.path("comments").get(0).path("line").asInt()).isEqualTo(10);
        assertThat(payload.path("comments").get(0).path("side").asText()).isEqualTo("RIGHT");
        assertThat(payload.path("comments").get(0).path("body").asText())
                .isEqualTo("Inline finding");
        assertThat(reviewId).isEqualTo("456");
    }

    @Test
    void deletePreviousReviewComments_deletesOnlyMarkedInlineComments() throws IOException {
        List<Request> requests = new ArrayList<>();
        when(okHttpClient.newCall(any(Request.class))).thenAnswer(invocation -> {
            Request request = invocation.getArgument(0);
            requests.add(request);

            String responseJson = request.method().equals("GET")
                    ? "[{\"id\":41,\"body\":\"old <!-- codecrow-analysis-review -->\"},"
                            + "{\"id\":42,\"body\":\"human review\"}]"
                    : "{}";
            Response requestResponse = new Response.Builder()
                    .request(request)
                    .protocol(Protocol.HTTP_1_1)
                    .code(200)
                    .message("OK")
                    .body(ResponseBody.create(responseJson, MediaType.parse("application/json")))
                    .build();
            Call requestCall = mock(Call.class);
            when(requestCall.execute()).thenReturn(requestResponse);
            return requestCall;
        });

        int deleted = action.deletePreviousReviewComments(
                "owner", "repo", 123, "<!-- codecrow-analysis-review -->");

        assertThat(deleted).isEqualTo(1);
        assertThat(requests).extracting(request -> request.method() + " " + request.url().encodedPath())
                .containsExactly(
                        "GET /repos/owner/repo/pulls/123/comments",
                        "DELETE /repos/owner/repo/pulls/comments/41"
                );
        assertThat(requests.get(0).url().queryParameter("per_page")).isEqualTo("100");
        assertThat(requests.get(0).url().queryParameter("page")).isEqualTo("1");
    }
}
