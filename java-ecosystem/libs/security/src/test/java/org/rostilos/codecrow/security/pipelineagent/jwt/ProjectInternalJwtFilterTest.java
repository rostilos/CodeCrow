package org.rostilos.codecrow.security.pipelineagent.jwt;

import jakarta.servlet.FilterChain;
import jakarta.servlet.ServletException;
import jakarta.servlet.http.HttpServletRequest;
import jakarta.servlet.http.HttpServletResponse;
import org.junit.jupiter.api.BeforeEach;
import org.junit.jupiter.api.Test;
import org.junit.jupiter.api.extension.ExtendWith;
import org.mockito.Mock;
import org.mockito.junit.jupiter.MockitoExtension;
import org.rostilos.codecrow.core.model.project.Project;
import org.rostilos.codecrow.core.persistence.repository.project.ProjectRepository;
import org.rostilos.codecrow.security.jwt.utils.JwtUtils;
import org.springframework.security.core.context.SecurityContextHolder;

import java.io.IOException;
import java.io.PrintWriter;
import java.io.StringWriter;
import java.util.Optional;

import static org.assertj.core.api.Assertions.assertThat;
import static org.mockito.Mockito.*;
import static org.mockito.Mockito.lenient;

@ExtendWith(MockitoExtension.class)
class ProjectInternalJwtFilterTest {

    @Mock
    private JwtUtils jwtUtils;

    @Mock
    private ProjectRepository projectRepository;

    @Mock
    private HttpServletRequest request;

    @Mock
    private HttpServletResponse response;

    @Mock
    private FilterChain filterChain;

    private ProjectInternalJwtFilter filter;
    private StringWriter responseWriter;

    @BeforeEach
    void setUp() throws IOException {
        SecurityContextHolder.clearContext();
        filter = new ProjectInternalJwtFilter(jwtUtils, projectRepository, "/actuator/health", "/api/webhooks/");
        responseWriter = new StringWriter();
        lenient().when(response.getWriter()).thenReturn(new PrintWriter(responseWriter));
    }

    @Test
    void testShouldNotFilter_ExcludedPath_ReturnsTrue() throws ServletException {
        when(request.getRequestURI()).thenReturn("/actuator/health");

        boolean result = filter.shouldNotFilter(request);

        assertThat(result).isTrue();
    }

    @Test
    void testShouldNotFilter_AnotherExcludedPath_ReturnsTrue() throws ServletException {
        when(request.getRequestURI()).thenReturn("/api/webhooks/bitbucket");

        boolean result = filter.shouldNotFilter(request);

        assertThat(result).isTrue();
    }

    @Test
    void testShouldNotFilter_NonExcludedPath_ReturnsFalse() throws ServletException {
        when(request.getRequestURI()).thenReturn("/api/processing/test");

        boolean result = filter.shouldNotFilter(request);

        assertThat(result).isFalse();
    }

    @Test
    void testDoFilterInternal_ValidJwt_SetsAuthentication() throws ServletException, IOException {
        String jwt = "valid.jwt.token";
        Project project = mock(Project.class);
        when(project.getId()).thenReturn(123L);
        when(project.getName()).thenReturn("Test Project");

        when(request.getRequestURI()).thenReturn("/api/processing/test");
        when(request.getHeader("Authorization")).thenReturn("Bearer " + jwt);
        when(jwtUtils.validateJwtToken(jwt)).thenReturn(true);
        when(jwtUtils.getUserNameFromJwtToken(jwt)).thenReturn("123");
        when(projectRepository.findByIdWithFullDetails(123L)).thenReturn(Optional.of(project));

        filter.doFilterInternal(request, response, filterChain);

        assertThat(SecurityContextHolder.getContext().getAuthentication()).isNotNull();
        verify(filterChain).doFilter(request, response);
    }

    @Test
    void testDoFilterInternal_MissingJwt_ReturnsUnauthorized() throws ServletException, IOException {
        when(request.getRequestURI()).thenReturn("/api/processing/test");
        when(request.getHeader("Authorization")).thenReturn(null);

        filter.doFilterInternal(request, response, filterChain);

        verify(response).setStatus(HttpServletResponse.SC_UNAUTHORIZED);
        verify(response).setContentType("application/json");
        assertThat(responseWriter.toString()).contains("invalid_token");
        verify(filterChain, never()).doFilter(any(), any());
    }

    @Test
    void testDoFilterInternal_InvalidJwt_ReturnsUnauthorized() throws ServletException, IOException {
        String jwt = "invalid.jwt.token";

        when(request.getRequestURI()).thenReturn("/api/processing/test");
        when(request.getHeader("Authorization")).thenReturn("Bearer " + jwt);
        when(jwtUtils.validateJwtToken(jwt)).thenReturn(false);

        filter.doFilterInternal(request, response, filterChain);

        verify(response).setStatus(HttpServletResponse.SC_UNAUTHORIZED);
        verify(response).setContentType("application/json");
        assertThat(responseWriter.toString()).contains("invalid_token");
        verify(filterChain, never()).doFilter(any(), any());
    }

    @Test
    void testDoFilterInternal_EmptySubject_ReturnsUnauthorized() throws ServletException, IOException {
        String jwt = "valid.jwt.token";

        when(request.getRequestURI()).thenReturn("/api/processing/test");
        when(request.getHeader("Authorization")).thenReturn("Bearer " + jwt);
        when(jwtUtils.validateJwtToken(jwt)).thenReturn(true);
        when(jwtUtils.getUserNameFromJwtToken(jwt)).thenReturn("");

        filter.doFilterInternal(request, response, filterChain);

        verify(response).setStatus(HttpServletResponse.SC_UNAUTHORIZED);
        verify(response).setContentType("application/json");
        assertThat(responseWriter.toString()).contains("invalid_token_subject");
        verify(filterChain, never()).doFilter(any(), any());
    }

    @Test
    void testDoFilterInternal_NonNumericSubject_ReturnsUnauthorized() throws ServletException, IOException {
        String jwt = "valid.jwt.token";

        when(request.getRequestURI()).thenReturn("/api/processing/test");
        when(request.getHeader("Authorization")).thenReturn("Bearer " + jwt);
        when(jwtUtils.validateJwtToken(jwt)).thenReturn(true);
        when(jwtUtils.getUserNameFromJwtToken(jwt)).thenReturn("not-a-number");

        filter.doFilterInternal(request, response, filterChain);

        verify(response).setStatus(HttpServletResponse.SC_UNAUTHORIZED);
        verify(response).setContentType("application/json");
        assertThat(responseWriter.toString()).contains("invalid_token_subject");
        verify(filterChain, never()).doFilter(any(), any());
    }

    @Test
    void testDoFilterInternal_ProjectNotFound_ReturnsNotFound() throws ServletException, IOException {
        String jwt = "valid.jwt.token";

        when(request.getRequestURI()).thenReturn("/api/processing/test");
        when(request.getHeader("Authorization")).thenReturn("Bearer " + jwt);
        when(jwtUtils.validateJwtToken(jwt)).thenReturn(true);
        when(jwtUtils.getUserNameFromJwtToken(jwt)).thenReturn("123");
        when(projectRepository.findByIdWithFullDetails(123L)).thenReturn(Optional.empty());

        filter.doFilterInternal(request, response, filterChain);

        verify(response).setStatus(HttpServletResponse.SC_NOT_FOUND);
        verify(response).setContentType("application/json");
        assertThat(responseWriter.toString()).contains("project_not_found");
        verify(filterChain, never()).doFilter(any(), any());
    }

    @Test
    void testDoFilterInternal_ExceptionDuringProcessing_ReturnsInternalError() throws ServletException, IOException {
        String jwt = "valid.jwt.token";

        when(request.getRequestURI()).thenReturn("/api/processing/test");
        when(request.getHeader("Authorization")).thenReturn("Bearer " + jwt);
        when(jwtUtils.validateJwtToken(jwt)).thenThrow(new RuntimeException("Unexpected error"));

        filter.doFilterInternal(request, response, filterChain);

        verify(response).setStatus(HttpServletResponse.SC_INTERNAL_SERVER_ERROR);
        verify(response).setContentType("application/json");
        assertThat(responseWriter.toString()).contains("internal_error");
        verify(filterChain, never()).doFilter(any(), any());
    }

    @Test
    void testDoFilterInternal_NonBearerToken_ReturnsUnauthorized() throws ServletException, IOException {
        when(request.getRequestURI()).thenReturn("/api/processing/test");
        when(request.getHeader("Authorization")).thenReturn("Basic sometoken");

        filter.doFilterInternal(request, response, filterChain);

        verify(response).setStatus(HttpServletResponse.SC_UNAUTHORIZED);
        verify(filterChain, never()).doFilter(any(), any());
    }
}
