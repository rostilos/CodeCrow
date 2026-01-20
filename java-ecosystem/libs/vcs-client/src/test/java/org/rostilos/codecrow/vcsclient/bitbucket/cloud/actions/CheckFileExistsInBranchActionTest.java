package org.rostilos.codecrow.vcsclient.bitbucket.cloud.actions;

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
class CheckFileExistsInBranchActionTest {

    @Mock
    private OkHttpClient okHttpClient;

    @Mock
    private Call call;

    @Mock
    private Response response;

    private CheckFileExistsInBranchAction action;

    @BeforeEach
    void setUp() {
        action = new CheckFileExistsInBranchAction(okHttpClient);
    }

    @Test
    void testFileExists_FileFound_ReturnsTrue() throws IOException {
        when(okHttpClient.newCall(any(Request.class))).thenReturn(call);
        when(call.execute()).thenReturn(response);
        when(response.isSuccessful()).thenReturn(true);

        boolean result = action.fileExists("workspace", "repo", "main", "src/main.java");

        assertThat(result).isTrue();
        verify(response).close();
    }

    @Test
    void testFileExists_FileNotFound_ReturnsFalse() throws IOException {
        when(okHttpClient.newCall(any(Request.class))).thenReturn(call);
        when(call.execute()).thenReturn(response);
        when(response.isSuccessful()).thenReturn(false);
        when(response.code()).thenReturn(404);

        boolean result = action.fileExists("workspace", "repo", "main", "nonexistent.java");

        assertThat(result).isFalse();
        verify(response).close();
    }

    @Test
    void testFileExists_UnexpectedResponseCode_ThrowsIOException() throws IOException {
        when(okHttpClient.newCall(any(Request.class))).thenReturn(call);
        when(call.execute()).thenReturn(response);
        when(response.isSuccessful()).thenReturn(false);
        when(response.code()).thenReturn(500);

        assertThatThrownBy(() -> action.fileExists("workspace", "repo", "main", "file.java"))
                .isInstanceOf(IOException.class)
                .hasMessageContaining("Unexpected response 500");

        verify(response).close();
    }

    @Test
    void testFileExists_EncodesFilePath() throws IOException {
        when(okHttpClient.newCall(any(Request.class))).thenReturn(call);
        when(call.execute()).thenReturn(response);
        when(response.isSuccessful()).thenReturn(true);

        action.fileExists("workspace", "repo", "main", "src/folder with spaces/file.java");

        verify(okHttpClient).newCall(argThat(request -> 
            request.url().toString().contains("folder%20with%20spaces") ||
            request.url().toString().contains("folder+with+spaces")
        ));
        verify(response).close();
    }

    @Test
    void testFileExists_HandlesNullWorkspace() throws IOException {
        when(okHttpClient.newCall(any(Request.class))).thenReturn(call);
        when(call.execute()).thenReturn(response);
        when(response.isSuccessful()).thenReturn(true);

        action.fileExists(null, "repo", "main", "file.java");

        verify(okHttpClient).newCall(argThat(request -> 
            request.url().toString().contains("/repositories//repo/src/")
        ));
        verify(response).close();
    }

    @Test
    void testFileExists_IOException_PropagatesException() throws IOException {
        when(okHttpClient.newCall(any(Request.class))).thenReturn(call);
        when(call.execute()).thenThrow(new IOException("Network error"));

        assertThatThrownBy(() -> action.fileExists("workspace", "repo", "main", "file.java"))
                .isInstanceOf(IOException.class)
                .hasMessage("Network error");
    }
}
