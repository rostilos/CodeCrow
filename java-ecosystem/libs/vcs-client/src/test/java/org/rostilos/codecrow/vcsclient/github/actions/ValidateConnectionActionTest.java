package org.rostilos.codecrow.vcsclient.github.actions;

import okhttp3.*;
import org.junit.jupiter.api.BeforeEach;
import org.junit.jupiter.api.Test;
import org.junit.jupiter.api.extension.ExtendWith;
import org.mockito.Mock;
import org.mockito.junit.jupiter.MockitoExtension;

import java.io.IOException;

import static org.assertj.core.api.Assertions.assertThat;
import static org.mockito.ArgumentMatchers.any;
import static org.mockito.Mockito.*;

@ExtendWith(MockitoExtension.class)
class ValidateConnectionActionTest {

    @Mock
    private OkHttpClient okHttpClient;

    @Mock
    private Call call;

    @Mock
    private Response response;

    private ValidateConnectionAction action;

    @BeforeEach
    void setUp() {
        action = new ValidateConnectionAction(okHttpClient);
    }

    @Test
    void testIsConnectionValid_SuccessfulResponse_ReturnsTrue() throws IOException {
        when(okHttpClient.newCall(any(Request.class))).thenReturn(call);
        when(call.execute()).thenReturn(response);
        when(response.isSuccessful()).thenReturn(true);

        boolean result = action.isConnectionValid();

        assertThat(result).isTrue();
        verify(response).close();
    }

    @Test
    void testIsConnectionValid_UnsuccessfulResponse_ReturnsFalse() throws IOException {
        when(okHttpClient.newCall(any(Request.class))).thenReturn(call);
        when(call.execute()).thenReturn(response);
        when(response.isSuccessful()).thenReturn(false);

        boolean result = action.isConnectionValid();

        assertThat(result).isFalse();
        verify(response).close();
    }

    @Test
    void testIsConnectionValid_IOException_ReturnsFalse() throws IOException {
        when(okHttpClient.newCall(any(Request.class))).thenReturn(call);
        when(call.execute()).thenThrow(new IOException("Network error"));

        boolean result = action.isConnectionValid();

        assertThat(result).isFalse();
    }
}
