package org.rostilos.codecrow.security.web.jwt;

import com.fasterxml.jackson.databind.ObjectMapper;
import jakarta.servlet.ServletException;
import jakarta.servlet.ServletOutputStream;
import jakarta.servlet.http.HttpServletRequest;
import jakarta.servlet.http.HttpServletResponse;
import org.junit.jupiter.api.BeforeEach;
import org.junit.jupiter.api.DisplayName;
import org.junit.jupiter.api.Test;
import org.junit.jupiter.api.extension.ExtendWith;
import org.mockito.ArgumentCaptor;
import org.mockito.Mock;
import org.mockito.junit.jupiter.MockitoExtension;
import org.springframework.security.core.AuthenticationException;

import java.io.ByteArrayOutputStream;
import java.io.IOException;
import java.util.Map;

import static org.assertj.core.api.Assertions.assertThat;
import static org.mockito.Mockito.*;

@ExtendWith(MockitoExtension.class)
@DisplayName("AuthEntryPoint")
class AuthEntryPointTest {

    private AuthEntryPoint authEntryPoint;

    @Mock
    private HttpServletRequest request;

    @Mock
    private HttpServletResponse response;

    @Mock
    private AuthenticationException authException;

    private ByteArrayOutputStream outputStream;

    @BeforeEach
    void setUp() throws IOException {
        authEntryPoint = new AuthEntryPoint();
        outputStream = new ByteArrayOutputStream();
        
        ServletOutputStream servletOutputStream = new ServletOutputStream() {
            @Override
            public boolean isReady() {
                return true;
            }

            @Override
            public void setWriteListener(jakarta.servlet.WriteListener writeListener) {
            }

            @Override
            public void write(int b) throws IOException {
                outputStream.write(b);
            }
        };
        
        lenient().when(response.getOutputStream()).thenReturn(servletOutputStream);
    }

    @Test
    @DisplayName("should set response status to 401 Unauthorized")
    void shouldSetResponseStatusTo401Unauthorized() throws IOException, ServletException {
        when(authException.getMessage()).thenReturn("Token expired");
        when(request.getServletPath()).thenReturn("/api/protected");

        authEntryPoint.commence(request, response, authException);

        verify(response).setStatus(HttpServletResponse.SC_UNAUTHORIZED);
    }

    @Test
    @DisplayName("should set content type to JSON")
    void shouldSetContentTypeToJson() throws IOException, ServletException {
        when(authException.getMessage()).thenReturn("Token expired");
        when(request.getServletPath()).thenReturn("/api/protected");

        authEntryPoint.commence(request, response, authException);

        verify(response).setContentType("application/json");
    }

    @Test
    @DisplayName("should write error details to response body")
    void shouldWriteErrorDetailsToResponseBody() throws IOException, ServletException {
        when(authException.getMessage()).thenReturn("Invalid token");
        when(request.getServletPath()).thenReturn("/api/users");

        authEntryPoint.commence(request, response, authException);

        String responseBody = outputStream.toString();
        ObjectMapper mapper = new ObjectMapper();
        Map<String, Object> body = mapper.readValue(responseBody, Map.class);

        assertThat(body.get("status")).isEqualTo(401);
        assertThat(body.get("error")).isEqualTo("Unauthorized");
        assertThat(body.get("message")).isEqualTo("Invalid token");
        assertThat(body.get("path")).isEqualTo("/api/users");
    }

    @Test
    @DisplayName("should include servlet path in response")
    void shouldIncludeServletPathInResponse() throws IOException, ServletException {
        when(authException.getMessage()).thenReturn("Auth error");
        when(request.getServletPath()).thenReturn("/api/admin/settings");

        authEntryPoint.commence(request, response, authException);

        String responseBody = outputStream.toString();
        assertThat(responseBody).contains("/api/admin/settings");
    }
}
