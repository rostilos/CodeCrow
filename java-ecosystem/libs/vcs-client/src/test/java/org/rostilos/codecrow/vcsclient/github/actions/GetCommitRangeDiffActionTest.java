package org.rostilos.codecrow.vcsclient.github.actions;

import okhttp3.*;
import org.junit.jupiter.api.BeforeEach;
import org.junit.jupiter.api.Test;
import org.junit.jupiter.api.extension.ExtendWith;
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
        String expectedDiff = "diff --git a/file.java b/file.java\n"
                + "--- a/file.java\n+++ b/file.java\n"
                + "@@ -1 +1 @@\n-old line\n+new line\n";

        when(okHttpClient.newCall(any(Request.class))).thenReturn(call);
        when(call.execute()).thenReturn(response);
        when(response.isSuccessful()).thenReturn(true);
        when(response.body()).thenReturn(responseBody);
        when(responseBody.string()).thenReturn(expectedDiff);

        String result = action.getCommitRangeDiff("owner", "repo", "abc123", "def456");

        assertThat(result).isEqualTo(expectedDiff);
        verify(okHttpClient).newCall(argThat(request ->
                request.url().toString().contains("compare/abc123...def456")
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

        assertThatThrownBy(() -> action.getCommitRangeDiff("owner", "repo", "invalid1", "invalid2"))
                .isInstanceOf(IOException.class)
                .hasMessageContaining("404");

        verify(response).close();
    }

    @Test
    void testGetCommitRangeDiff_IOException_PropagatesException() throws IOException {
        when(okHttpClient.newCall(any(Request.class))).thenReturn(call);
        when(call.execute()).thenThrow(new IOException("Network error"));

        assertThatThrownBy(() -> action.getCommitRangeDiff("owner", "repo", "abc123", "def456"))
                .isInstanceOf(IOException.class)
                .hasMessage("Network error");
    }

    @Test
    void testGetCommitRangeDiff_UsesThreeDotsSyntax() throws IOException {
        when(okHttpClient.newCall(any(Request.class))).thenReturn(call);
        when(call.execute()).thenReturn(response);
        when(response.isSuccessful()).thenReturn(true);
        when(response.body()).thenReturn(responseBody);
        when(responseBody.string()).thenReturn("");

        action.getCommitRangeDiff("owner", "repo", "base", "head");

        verify(okHttpClient).newCall(argThat(request ->
                request.url().toString().contains("base...head")
        ));
    }

    @Test
    void successfulResponseWithoutBodyIsNotAnAuthoritativeEmptyDiff() throws IOException {
        when(okHttpClient.newCall(any(Request.class))).thenReturn(call);
        when(call.execute()).thenReturn(response);
        when(response.isSuccessful()).thenReturn(true);
        when(response.body()).thenReturn(null);

        assertThatThrownBy(() -> action.getCommitRangeDiff(
                "owner", "repo", "a".repeat(40), "b".repeat(40)))
                .isInstanceOfSatisfying(DiffAcquisitionException.class, exception ->
                        assertThat(exception.reason())
                                .isEqualTo(ExactDiffInventory.GapType.PATCH_UNAVAILABLE));
    }

    @Test
    void nonBlankMalformedRawDiffFailsClosed() throws IOException {
        when(okHttpClient.newCall(any(Request.class))).thenReturn(call);
        when(call.execute()).thenReturn(response);
        when(response.isSuccessful()).thenReturn(true);
        when(response.body()).thenReturn(responseBody);
        when(responseBody.string()).thenReturn("upstream returned OK without a unified diff");

        assertThatThrownBy(() -> action.getCommitRangeDiff(
                "owner", "repo", "a".repeat(40), "b".repeat(40)))
                .isInstanceOfSatisfying(DiffAcquisitionException.class, exception ->
                        assertThat(exception.reason())
                                .isEqualTo(ExactDiffInventory.GapType.MALFORMED));
    }

    @Test
    void zeroByteProviderDiffIsAnAuthoritativeEmptyComparison() throws IOException {
        when(okHttpClient.newCall(any(Request.class))).thenReturn(call);
        when(call.execute()).thenReturn(response);
        when(response.isSuccessful()).thenReturn(true);
        when(response.body()).thenReturn(responseBody);
        when(responseBody.string()).thenReturn("");

        assertThat(action.getCommitRangeDiff(
                "owner", "repo", "a".repeat(40), "b".repeat(40)))
                .isEmpty();
    }
}
