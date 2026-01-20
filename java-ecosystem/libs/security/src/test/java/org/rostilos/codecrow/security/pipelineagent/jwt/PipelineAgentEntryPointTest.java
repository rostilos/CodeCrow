package org.rostilos.codecrow.security.pipelineagent.jwt;

import jakarta.servlet.ServletException;
import jakarta.servlet.http.HttpServletRequest;
import jakarta.servlet.http.HttpServletResponse;
import org.junit.jupiter.api.BeforeEach;
import org.junit.jupiter.api.DisplayName;
import org.junit.jupiter.api.Test;
import org.junit.jupiter.api.extension.ExtendWith;
import org.mockito.Mock;
import org.mockito.junit.jupiter.MockitoExtension;
import org.springframework.security.core.AuthenticationException;

import java.io.IOException;

import static org.mockito.Mockito.verify;

@ExtendWith(MockitoExtension.class)
@DisplayName("PipelineAgentEntryPoint")
class PipelineAgentEntryPointTest {

    private PipelineAgentEntryPoint entryPoint;

    @Mock
    private HttpServletRequest request;

    @Mock
    private HttpServletResponse response;

    @Mock
    private AuthenticationException authException;

    @BeforeEach
    void setUp() {
        entryPoint = new PipelineAgentEntryPoint();
    }

    @Test
    @DisplayName("should send 401 Unauthorized error")
    void shouldSend401UnauthorizedError() throws IOException, ServletException {
        entryPoint.commence(request, response, authException);

        verify(response).sendError(HttpServletResponse.SC_UNAUTHORIZED, "Unauthorized");
    }

    @Test
    @DisplayName("should send 401 regardless of exception message")
    void shouldSend401RegardlessOfExceptionMessage() throws IOException, ServletException {
        entryPoint.commence(request, response, authException);

        verify(response).sendError(401, "Unauthorized");
    }
}
