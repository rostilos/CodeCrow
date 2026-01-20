package org.rostilos.codecrow.vcsclient.gitlab.actions;

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

    @Mock
    private ResponseBody responseBody;

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
        when(response.code()).thenReturn(200);

        boolean result = action.fileExists("namespace", "project", "main", "src/main.java");

        assertThat(result).isTrue();
        verify(response).close();
    }

    @Test
    void testFileExists_FileNotFound_ReturnsFalse() throws IOException {
        when(okHttpClient.newCall(any(Request.class))).thenReturn(call);
        when(call.execute()).thenReturn(response);
        when(response.code()).thenReturn(404);

        boolean result = action.fileExists("namespace", "project", "main", "nonexistent.java");

        assertThat(result).isFalse();
        verify(response).close();
    }

    @Test
    void testFileExists_UnsuccessfulNon404Response_ThrowsIOException() throws IOException {
        when(okHttpClient.newCall(any(Request.class))).thenReturn(call);
        when(call.execute()).thenReturn(response);
        when(response.code()).thenReturn(500);
        when(response.isSuccessful()).thenReturn(false);
        when(response.body()).thenReturn(responseBody);
        when(responseBody.string()).thenReturn("Internal Server Error");

        assertThatThrownBy(() -> action.fileExists("namespace", "project", "main", "file.java"))
                .isInstanceOf(IOException.class)
                .hasMessageContaining("Failed to check file existence: 500");

        verify(response).close();
    }

    @Test
    void testFileExists_EncodesProjectPath() throws IOException {
        when(okHttpClient.newCall(any(Request.class))).thenReturn(call);
        when(call.execute()).thenReturn(response);
        when(response.isSuccessful()).thenReturn(true);
        when(response.code()).thenReturn(200);

        action.fileExists("my-group", "my project", "main", "file.java");

        verify(okHttpClient).newCall(argThat(request -> 
            request.url().toString().contains("my-group%2Fmy+project") || 
            request.url().toString().contains("my-group%2Fmy%20project")
        ));
        verify(response).close();
    }

    @Test
    void testFileExists_EncodesFilePath() throws IOException {
        when(okHttpClient.newCall(any(Request.class))).thenReturn(call);
        when(call.execute()).thenReturn(response);
        when(response.isSuccessful()).thenReturn(true);
        when(response.code()).thenReturn(200);

        action.fileExists("namespace", "project", "main", "src/folder with spaces/file.java");

        verify(okHttpClient).newCall(argThat(request -> 
            request.url().toString().contains("folder+with+spaces") ||
            request.url().toString().contains("folder%20with%20spaces")
        ));
        verify(response).close();
    }

    @Test
    void testFileExists_EncodesRefParameter() throws IOException {
        when(okHttpClient.newCall(any(Request.class))).thenReturn(call);
        when(call.execute()).thenReturn(response);
        when(response.isSuccessful()).thenReturn(true);
        when(response.code()).thenReturn(200);

        action.fileExists("namespace", "project", "feature/my-branch", "file.java");

        verify(okHttpClient).newCall(argThat(request -> 
            request.url().toString().contains("ref=feature%2Fmy-branch")
        ));
        verify(response).close();
    }

    @Test
    void testFileExists_IOException_PropagatesException() throws IOException {
        when(okHttpClient.newCall(any(Request.class))).thenReturn(call);
        when(call.execute()).thenThrow(new IOException("Network error"));

        assertThatThrownBy(() -> action.fileExists("namespace", "project", "main", "file.java"))
                .isInstanceOf(IOException.class)
                .hasMessage("Network error");
    }
}
