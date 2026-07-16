package org.rostilos.codecrow.vcsclient.gitlab.actions;

import okhttp3.Call;
import okhttp3.OkHttpClient;
import okhttp3.Request;
import okhttp3.Response;
import okhttp3.ResponseBody;
import org.junit.jupiter.api.BeforeEach;
import org.junit.jupiter.api.Test;
import org.junit.jupiter.api.extension.ExtendWith;
import org.junit.jupiter.params.ParameterizedTest;
import org.junit.jupiter.params.provider.ValueSource;
import org.mockito.Mock;
import org.mockito.junit.jupiter.MockitoExtension;
import org.rostilos.codecrow.vcsclient.diff.DiffAcquisitionException;
import org.rostilos.codecrow.vcsclient.diff.ExactDiffInventory;

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
                        "diff": "@@ -1 +1 @@\\n-old line\\n+new line\\n",
                        "new_path": "file.java",
                        "old_path": "file.java",
                        "a_mode": "100644",
                        "b_mode": "100644"
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

    @Test
    void unsuccessfulResponseWithoutBodyStillReportsTheFailure() throws IOException {
        when(okHttpClient.newCall(any(Request.class))).thenReturn(call);
        when(call.execute()).thenReturn(response);
        when(response.isSuccessful()).thenReturn(false);
        when(response.code()).thenReturn(502);
        when(response.body()).thenReturn(null);

        assertThatThrownBy(() -> action.getCommitRangeDiff(
                "namespace", "project", "base", "head"))
                .isInstanceOf(IOException.class)
                .hasMessageContaining("502");
    }

    @Test
    void successfulResponseWithoutABodyFailsClosed() throws IOException {
        when(okHttpClient.newCall(any(Request.class))).thenReturn(call);
        when(call.execute()).thenReturn(response);
        when(response.isSuccessful()).thenReturn(true);
        when(response.body()).thenReturn(null);

        assertThatThrownBy(() -> action.getCommitRangeDiff(
                "namespace", "project", "a".repeat(40), "b".repeat(40)))
                .isInstanceOfSatisfying(DiffAcquisitionException.class, exception ->
                        assertThat(exception.reason())
                                .isEqualTo(ExactDiffInventory.GapType.PATCH_UNAVAILABLE));
    }

    @ParameterizedTest
    @ValueSource(strings = {"", "null", "[]", "{}", "{\"diffs\":{}}"})
    void successfulResponseWithoutTypedDiffInventoryFailsClosed(String responseJson)
            throws IOException {
        when(okHttpClient.newCall(any(Request.class))).thenReturn(call);
        when(call.execute()).thenReturn(response);
        when(response.isSuccessful()).thenReturn(true);
        when(response.body()).thenReturn(responseBody);
        when(responseBody.string()).thenReturn(responseJson);

        assertThatThrownBy(() -> action.getCommitRangeDiff(
                "namespace", "project", "a".repeat(40), "b".repeat(40)))
                .isInstanceOfSatisfying(DiffAcquisitionException.class, exception ->
                        assertThat(exception.reason())
                                .isEqualTo(ExactDiffInventory.GapType.MALFORMED));
    }

    @Test
    void explicitEmptyDiffInventoryRemainsAnAuthoritativeEmptyComparison()
            throws IOException {
        when(okHttpClient.newCall(any(Request.class))).thenReturn(call);
        when(call.execute()).thenReturn(response);
        when(response.isSuccessful()).thenReturn(true);
        when(response.body()).thenReturn(responseBody);
        when(responseBody.string()).thenReturn("{\"diffs\":[]}");

        assertThat(action.getCommitRangeDiff(
                "namespace", "project", "a".repeat(40), "b".repeat(40)))
                .isEmpty();
    }

    @Test
    void internalParserRejectsAJsonDocumentWithoutARootValue() throws Exception {
        java.lang.reflect.Method method = GetCommitRangeDiffAction.class.getDeclaredMethod(
                "buildUnifiedDiff", String.class);
        method.setAccessible(true);

        assertThatThrownBy(() -> method.invoke(action, " \n\t"))
                .hasCauseInstanceOf(IOException.class)
                .cause()
                .hasMessageContaining("JSON object");
    }

    @ParameterizedTest
    @ValueSource(strings = {
            "{\"diffs\":[{\"old_path\":\"a.txt\",\"new_path\":\"a.txt\",\"diff\":\"\",\"too_large\":true}]}",
            "{\"diffs\":[{\"old_path\":\"a.txt\",\"new_path\":\"a.txt\",\"diff\":\"\",\"collapsed\":true}]}"
    })
    void providerTruncationIsATypedNonCleanAcquisition(String jsonResponse)
            throws IOException {
        when(okHttpClient.newCall(any(Request.class))).thenReturn(call);
        when(call.execute()).thenReturn(response);
        when(response.isSuccessful()).thenReturn(true);
        when(response.body()).thenReturn(responseBody);
        when(responseBody.string()).thenReturn(jsonResponse);

        assertThatThrownBy(() -> action.getCommitRangeDiff(
                "namespace", "project", "a".repeat(40), "b".repeat(40)))
                .isInstanceOfSatisfying(DiffAcquisitionException.class, exception ->
                        assertThat(exception.reason())
                                .isEqualTo(ExactDiffInventory.GapType.PROVIDER_TRUNCATED));
    }

    @ParameterizedTest
    @ValueSource(strings = {
            "{\"diffs\":[{}]}",
            "{\"diffs\":[null]}",
            "{\"diffs\":[{\"old_path\":\"\",\"new_path\":\"\",\"diff\":\"@@ -1 +1 @@\"}]}",
            "{\"diffs\":[{\"old_path\":\"a.txt\",\"new_path\":\"a.txt\",\"diff\":{\"unexpected\":true}}]}"
    })
    void malformedTypedEntriesFailInsteadOfSynthesizingEmptyPaths(String jsonResponse)
            throws IOException {
        when(okHttpClient.newCall(any(Request.class))).thenReturn(call);
        when(call.execute()).thenReturn(response);
        when(response.isSuccessful()).thenReturn(true);
        when(response.body()).thenReturn(responseBody);
        when(responseBody.string()).thenReturn(jsonResponse);

        assertThatThrownBy(() -> action.getCommitRangeDiff(
                "namespace", "project", "a".repeat(40), "b".repeat(40)))
                .isInstanceOfSatisfying(DiffAcquisitionException.class, exception ->
                        assertThat(exception.reason())
                                .isEqualTo(ExactDiffInventory.GapType.MALFORMED));
    }
}
