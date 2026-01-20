package org.rostilos.codecrow.security.web;

import jakarta.servlet.FilterChain;
import jakarta.servlet.ServletException;
import jakarta.servlet.http.HttpServletRequest;
import jakarta.servlet.http.HttpServletResponse;
import org.junit.jupiter.api.BeforeEach;
import org.junit.jupiter.api.Test;
import org.junit.jupiter.api.extension.ExtendWith;
import org.mockito.Mock;
import org.mockito.junit.jupiter.MockitoExtension;

import java.io.IOException;
import java.io.PrintWriter;
import java.io.StringWriter;
import java.lang.reflect.Field;

import static org.assertj.core.api.Assertions.assertThat;
import static org.mockito.Mockito.*;
import static org.mockito.Mockito.lenient;

@ExtendWith(MockitoExtension.class)
class InternalApiSecurityFilterTest {

    @Mock
    private HttpServletRequest request;

    @Mock
    private HttpServletResponse response;

    @Mock
    private FilterChain filterChain;

    private InternalApiSecurityFilter filter;

    private StringWriter responseWriter;

    private void setInternalApiSecret(String secret) throws Exception {
        Field field = InternalApiSecurityFilter.class.getDeclaredField("internalApiSecret");
        field.setAccessible(true);
        field.set(filter, secret);
    }

    @BeforeEach
    void setUp() throws IOException {
        filter = new InternalApiSecurityFilter();
        responseWriter = new StringWriter();
        lenient().when(response.getWriter()).thenReturn(new PrintWriter(responseWriter));
    }

    @Test
    void testDoFilterInternal_NonInternalApi_PassesThrough() throws Exception {
        setInternalApiSecret("test-secret");
        when(request.getRequestURI()).thenReturn("/api/public/test");

        filter.doFilterInternal(request, response, filterChain);

        verify(filterChain).doFilter(request, response);
        verify(response, never()).setStatus(anyInt());
    }

    @Test
    void testDoFilterInternal_InternalApi_ValidSecret_PassesThrough() throws Exception {
        setInternalApiSecret("test-secret");
        when(request.getRequestURI()).thenReturn("/api/internal/test");
        when(request.getHeader("X-Internal-Secret")).thenReturn("test-secret");

        filter.doFilterInternal(request, response, filterChain);

        verify(filterChain).doFilter(request, response);
        verify(response, never()).setStatus(anyInt());
    }

    @Test
    void testDoFilterInternal_InternalApi_SecretNotConfigured_Returns403() throws Exception {
        setInternalApiSecret("");
        when(request.getRequestURI()).thenReturn("/api/internal/test");

        filter.doFilterInternal(request, response, filterChain);

        verify(response).setStatus(HttpServletResponse.SC_FORBIDDEN);
        verify(response).setContentType("application/json");
        assertThat(responseWriter.toString()).contains("Internal API not available");
        verify(filterChain, never()).doFilter(any(), any());
    }

    @Test
    void testDoFilterInternal_InternalApi_SecretBlank_Returns403() throws Exception {
        setInternalApiSecret("   ");
        when(request.getRequestURI()).thenReturn("/api/internal/test");

        filter.doFilterInternal(request, response, filterChain);

        verify(response).setStatus(HttpServletResponse.SC_FORBIDDEN);
        verify(response).setContentType("application/json");
        assertThat(responseWriter.toString()).contains("Internal API not available");
        verify(filterChain, never()).doFilter(any(), any());
    }

    @Test
    void testDoFilterInternal_InternalApi_MissingHeader_Returns401() throws Exception {
        setInternalApiSecret("test-secret");
        when(request.getRequestURI()).thenReturn("/api/internal/test");
        when(request.getHeader("X-Internal-Secret")).thenReturn(null);

        filter.doFilterInternal(request, response, filterChain);

        verify(response).setStatus(HttpServletResponse.SC_UNAUTHORIZED);
        verify(response).setContentType("application/json");
        assertThat(responseWriter.toString()).contains("Missing authentication");
        verify(filterChain, never()).doFilter(any(), any());
    }

    @Test
    void testDoFilterInternal_InternalApi_BlankHeader_Returns401() throws Exception {
        setInternalApiSecret("test-secret");
        when(request.getRequestURI()).thenReturn("/api/internal/test");
        when(request.getHeader("X-Internal-Secret")).thenReturn("   ");

        filter.doFilterInternal(request, response, filterChain);

        verify(response).setStatus(HttpServletResponse.SC_UNAUTHORIZED);
        verify(response).setContentType("application/json");
        assertThat(responseWriter.toString()).contains("Missing authentication");
        verify(filterChain, never()).doFilter(any(), any());
    }

    @Test
    void testDoFilterInternal_InternalApi_InvalidSecret_Returns401() throws Exception {
        setInternalApiSecret("test-secret");
        when(request.getRequestURI()).thenReturn("/api/internal/test");
        when(request.getHeader("X-Internal-Secret")).thenReturn("wrong-secret");

        filter.doFilterInternal(request, response, filterChain);

        verify(response).setStatus(HttpServletResponse.SC_UNAUTHORIZED);
        verify(response).setContentType("application/json");
        assertThat(responseWriter.toString()).contains("Invalid authentication");
        verify(filterChain, never()).doFilter(any(), any());
    }

    @Test
    void testDoFilterInternal_InternalApi_NullSecret_Returns403() throws Exception {
        setInternalApiSecret(null);
        when(request.getRequestURI()).thenReturn("/api/internal/test");

        filter.doFilterInternal(request, response, filterChain);

        verify(response).setStatus(HttpServletResponse.SC_FORBIDDEN);
        verify(filterChain, never()).doFilter(any(), any());
    }
}
