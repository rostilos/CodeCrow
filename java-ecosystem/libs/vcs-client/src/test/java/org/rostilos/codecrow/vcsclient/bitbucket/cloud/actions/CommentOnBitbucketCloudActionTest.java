package org.rostilos.codecrow.vcsclient.bitbucket.cloud.actions;

import com.fasterxml.jackson.databind.JsonNode;
import com.fasterxml.jackson.databind.ObjectMapper;
import okhttp3.*;
import org.junit.jupiter.api.BeforeEach;
import org.junit.jupiter.api.Nested;
import org.junit.jupiter.api.Test;
import org.junit.jupiter.api.extension.ExtendWith;
import org.mockito.ArgumentCaptor;
import org.mockito.Mock;
import org.mockito.junit.jupiter.MockitoExtension;
import org.rostilos.codecrow.core.model.vcs.VcsRepoInfo;

import java.io.IOException;

import static org.assertj.core.api.Assertions.assertThat;
import static org.assertj.core.api.Assertions.assertThatThrownBy;
import static org.mockito.ArgumentMatchers.any;
import static org.mockito.Mockito.*;

@ExtendWith(MockitoExtension.class)
class CommentOnBitbucketCloudActionTest {

    @Mock private OkHttpClient httpClient;
    @Mock private VcsRepoInfo vcsRepoInfo;
    @Mock private Call call;

    private final ObjectMapper mapper = new ObjectMapper();

    private CommentOnBitbucketCloudAction action;

    @BeforeEach
    void setUp() {
        lenient().when(vcsRepoInfo.getRepoWorkspace()).thenReturn("my-workspace");
        lenient().when(vcsRepoInfo.getRepoSlug()).thenReturn("my-repo");
        action = new CommentOnBitbucketCloudAction(httpClient, vcsRepoInfo, 42L);
    }

    private Response jsonResponse(Request req, int code, String body) {
        return new Response.Builder()
                .request(req)
                .protocol(Protocol.HTTP_1_1)
                .code(code)
                .message(code >= 200 && code < 300 ? "OK" : "Error")
                .body(ResponseBody.create(body, MediaType.parse("application/json")))
                .build();
    }

    // ── postSummaryResultWithId ──────────────────────────────────────────

    @Nested
    class PostSummaryResult {

        @Test
        void validPost_shouldReturnCommentId() throws Exception {
            // First call: GET comments for deletion (empty)
            // Second call: POST comment
            ArgumentCaptor<Request> captor = ArgumentCaptor.forClass(Request.class);
            when(httpClient.newCall(captor.capture())).thenReturn(call);

            // First call = GET for delete old comments
            // Second call = POST new comment
            when(call.execute())
                    .thenAnswer(inv -> jsonResponse(captor.getValue(), 200, "{\"values\":[], \"next\":null}"))
                    .thenAnswer(inv -> jsonResponse(captor.getValue(), 201, "{\"id\":999}"));

            String id = action.postSummaryResultWithId("Hello PR!");

            assertThat(id).isEqualTo("999");
            verify(httpClient, atLeast(2)).newCall(any());
        }

        @Test
        void uuidWorkspace_shouldThrowIOException() {
            when(vcsRepoInfo.getRepoWorkspace()).thenReturn("{uuid-value}");
            action = new CommentOnBitbucketCloudAction(httpClient, vcsRepoInfo, 1L);

            assertThatThrownBy(() -> action.postSummaryResultWithId("text"))
                    .isInstanceOf(IOException.class)
                    .hasMessageContaining("Invalid workspace format");
        }

        @Test
        void uuidRepoSlug_shouldThrowIOException() {
            when(vcsRepoInfo.getRepoSlug()).thenReturn("{repo-uuid}");
            action = new CommentOnBitbucketCloudAction(httpClient, vcsRepoInfo, 1L);

            assertThatThrownBy(() -> action.postSummaryResultWithId("text"))
                    .isInstanceOf(IOException.class)
                    .hasMessageContaining("Invalid repository format");
        }

        @Test
        void httpError_shouldThrowIOException() throws Exception {
            ArgumentCaptor<Request> captor = ArgumentCaptor.forClass(Request.class);
            when(httpClient.newCall(captor.capture())).thenReturn(call);
            // GET comments succeeds, POST fails
            when(call.execute())
                    .thenAnswer(inv -> jsonResponse(captor.getValue(), 200, "{\"values\":[]}"))
                    .thenAnswer(inv -> jsonResponse(captor.getValue(), 400, "Bad request"));

            assertThatThrownBy(() -> action.postSummaryResultWithId("content"))
                    .isInstanceOf(IOException.class);
        }
    }

    // ── postCommentReply ─────────────────────────────────────────────────

    @Nested
    class PostCommentReply {

        @Test
        void validReply_shouldReturnId() throws Exception {
            ArgumentCaptor<Request> captor = ArgumentCaptor.forClass(Request.class);
            when(httpClient.newCall(captor.capture())).thenReturn(call);
            when(call.execute()).thenAnswer(inv -> jsonResponse(captor.getValue(), 201, "{\"id\":\"100\"}"));

            String result = action.postCommentReply("50", "reply text");

            assertThat(result).isEqualTo("100");
            Request req = captor.getValue();
            assertThat(req.url().toString()).contains("/pullrequests/42/comments");

            // Verify body has parent.id
            okio.Buffer buf = new okio.Buffer();
            req.body().writeTo(buf);
            JsonNode json = mapper.readTree(buf.readUtf8());
            assertThat(json.path("parent").path("id").asInt()).isEqualTo(50);
            assertThat(json.path("content").path("raw").asText()).isEqualTo("reply text");
        }

        @Test
        void uuidWorkspace_shouldThrow() {
            when(vcsRepoInfo.getRepoWorkspace()).thenReturn("{uuid}");
            action = new CommentOnBitbucketCloudAction(httpClient, vcsRepoInfo, 1L);

            assertThatThrownBy(() -> action.postCommentReply("1", "text"))
                    .isInstanceOf(IOException.class);
        }
    }

    // ── postSimpleComment ────────────────────────────────────────────────

    @Test
    void postSimpleComment_shouldReturnId() throws Exception {
        ArgumentCaptor<Request> captor = ArgumentCaptor.forClass(Request.class);
        when(httpClient.newCall(captor.capture())).thenReturn(call);
        when(call.execute()).thenAnswer(inv -> jsonResponse(captor.getValue(), 201, "{\"id\":\"77\"}"));

        String result = action.postSimpleComment("simple content");
        assertThat(result).isEqualTo("77");
    }

    // ── deleteCommentById ────────────────────────────────────────────────

    @Test
    void deleteCommentById_shouldSendDelete() throws Exception {
        ArgumentCaptor<Request> captor = ArgumentCaptor.forClass(Request.class);
        when(httpClient.newCall(captor.capture())).thenReturn(call);
        when(call.execute()).thenAnswer(inv -> jsonResponse(captor.getValue(), 204, ""));

        action.deleteCommentById("123");

        Request req = captor.getValue();
        assertThat(req.method()).isEqualTo("DELETE");
        assertThat(req.url().toString()).contains("/comments/123");
    }

    // ── updateComment ────────────────────────────────────────────────────

    @Test
    void updateComment_shouldSendPut() throws Exception {
        ArgumentCaptor<Request> captor = ArgumentCaptor.forClass(Request.class);
        when(httpClient.newCall(captor.capture())).thenReturn(call);
        when(call.execute()).thenAnswer(inv -> jsonResponse(captor.getValue(), 200, "{\"id\":\"123\"}"));

        action.updateComment("123", "updated content");

        Request req = captor.getValue();
        assertThat(req.method()).isEqualTo("PUT");
        assertThat(req.url().toString()).contains("/comments/123");
    }

    // ── deleteCommentsByMarker ───────────────────────────────────────────

    @Test
    void deleteCommentsByMarker_shouldDeleteMatching() throws Exception {
        String commentsJson = """
                {
                    "values": [
                        {"id": "1", "content": {"raw": "[MARKER] some text"}, "links": {"self": {"href": "url"}}},
                        {"id": "2", "content": {"raw": "no marker"}, "links": {"self": {"href": "url"}}},
                        {"id": "3", "content": {"raw": "[MARKER] another"}, "links": {"self": {"href": "url"}}}
                    ]
                }
                """;

        ArgumentCaptor<Request> captor = ArgumentCaptor.forClass(Request.class);
        when(httpClient.newCall(captor.capture())).thenReturn(call);
        when(call.execute())
                .thenAnswer(inv -> jsonResponse(captor.getValue(), 200, commentsJson))
                .thenAnswer(inv -> jsonResponse(captor.getValue(), 204, ""))
                .thenAnswer(inv -> jsonResponse(captor.getValue(), 204, ""));

        int deleted = action.deleteCommentsByMarker("[MARKER]");
        assertThat(deleted).isEqualTo(2);
    }

    @Test
    void deleteCommentsByMarker_noMatches_shouldReturnZero() throws Exception {
        ArgumentCaptor<Request> captor = ArgumentCaptor.forClass(Request.class);
        when(httpClient.newCall(captor.capture())).thenReturn(call);
        when(call.execute()).thenAnswer(inv -> jsonResponse(captor.getValue(), 200,
                "{\"values\":[{\"id\":\"1\",\"content\":{\"raw\":\"no match\"}}]}"));

        int deleted = action.deleteCommentsByMarker("[MARKER]");
        assertThat(deleted).isEqualTo(0);
    }

    @Test
    void deleteCommentsByMarker_fetchFails_shouldThrow() throws Exception {
        ArgumentCaptor<Request> captor = ArgumentCaptor.forClass(Request.class);
        when(httpClient.newCall(captor.capture())).thenReturn(call);
        when(call.execute()).thenAnswer(inv -> jsonResponse(captor.getValue(), 500, "server error"));

        assertThatThrownBy(() -> action.deleteCommentsByMarker("[M]"))
                .isInstanceOf(IOException.class);
    }
}
