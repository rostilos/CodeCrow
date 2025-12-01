package org.rostilos.codecrow.security.pipelineagent.jwt;

import java.io.IOException;
import java.util.Arrays;
import java.util.List;
import java.util.Optional;

import jakarta.servlet.FilterChain;
import jakarta.servlet.ServletException;
import jakarta.servlet.http.HttpServletRequest;
import jakarta.servlet.http.HttpServletResponse;

import org.rostilos.codecrow.core.dto.project.ProjectDTO;
import org.rostilos.codecrow.core.model.project.Project;
import org.rostilos.codecrow.core.persistence.repository.project.ProjectRepository;
import org.rostilos.codecrow.security.jwt.utils.JwtUtils;
import org.slf4j.Logger;
import org.slf4j.LoggerFactory;
import org.springframework.security.authentication.UsernamePasswordAuthenticationToken;
import org.springframework.security.core.context.SecurityContextHolder;
import org.springframework.util.StringUtils;
import org.springframework.web.filter.OncePerRequestFilter;

/**
 * Validates incoming requests to /api/processing/** by checking a JWT in the
 * Authorization header. The JWT subject is expected to contain the project id
 * (as a plain decimal string).
 *
 * This filter authenticates based on the project ID in the JWT token and
 * sets up the Spring Security context with the project information.
 */
public class ProjectInternalJwtFilter extends OncePerRequestFilter {

    private static final Logger logger = LoggerFactory.getLogger(ProjectInternalJwtFilter.class);

    private final JwtUtils jwtUtils;
    private final ProjectRepository projectRepository;
    private final List<String> pathsToExclude;

    public ProjectInternalJwtFilter(JwtUtils jwtUtils, ProjectRepository projectRepository, String... excludePaths) {
        this.jwtUtils = jwtUtils;
        this.projectRepository = projectRepository;
        this.pathsToExclude = Arrays.asList(excludePaths);
    }

    @Override
    protected boolean shouldNotFilter(HttpServletRequest request) throws ServletException {
        String path = request.getRequestURI();

        boolean shouldExclude = pathsToExclude.stream()
                .anyMatch(excludePath -> path.endsWith(excludePath) || path.startsWith(excludePath));

        if (shouldExclude) {
            logger.debug("Skipping InternalJwtFilter for excluded path: {}", path);
        }
        return shouldExclude;
    }

    @Override
    protected void doFilterInternal(
            HttpServletRequest request,
            HttpServletResponse response,
            FilterChain filterChain
    ) throws IOException, ServletException {

        String path = request.getRequestURI();

        try {
            // Validate JWT token
            String jwt = parseJwt(request);
            if (jwt == null || !jwtUtils.validateJwtToken(jwt)) {
                logger.warn("Missing or invalid JWT for processing endpoint: {}", path);
                sendErrorResponse(response, HttpServletResponse.SC_UNAUTHORIZED, "invalid_token");
                return;
            }

            // Extract project ID from JWT subject
            String subject = jwtUtils.getUserNameFromJwtToken(jwt);
            if (subject == null || subject.isBlank()) {
                logger.warn("JWT subject is empty");
                sendErrorResponse(response, HttpServletResponse.SC_UNAUTHORIZED, "invalid_token_subject");
                return;
            }

            // Parse project ID from JWT subject
            long projectIdFromJwt;
            try {
                projectIdFromJwt = Long.parseLong(subject);
            } catch (NumberFormatException e) {
                logger.warn("JWT subject is not a valid project ID: {}", subject);
                sendErrorResponse(response, HttpServletResponse.SC_UNAUTHORIZED, "invalid_token_subject");
                return;
            }

            // Verify project exists
            Optional<Project> optionalProject = projectRepository.findById(projectIdFromJwt);
            if (!optionalProject.isPresent()) {
                logger.warn("Project with id {} not found", projectIdFromJwt);
                sendErrorResponse(response, HttpServletResponse.SC_NOT_FOUND, "project_not_found");
                return;
            }

            // Set up Spring Security context
            Project project = optionalProject.get();
            ProjectDTO projectDTO = ProjectDTO.fromProject(project);

            UsernamePasswordAuthenticationToken authentication = new UsernamePasswordAuthenticationToken(
                    projectDTO, null, List.of()
            );
            SecurityContextHolder.getContext().setAuthentication(authentication);

            logger.debug("Successfully authenticated request for project ID: {}", projectIdFromJwt);

            // Continue with the original request
            filterChain.doFilter(request, response);

        } catch (Exception e) {
            logger.error("Error during internal JWT validation", e);
            sendErrorResponse(response, HttpServletResponse.SC_INTERNAL_SERVER_ERROR, "internal_error");
        }
    }

    private void sendErrorResponse(HttpServletResponse response, int status, String error) throws IOException {
        response.setStatus(status);
        response.setContentType("application/json");
        response.getWriter().write("{\"error\":\"" + error + "\"}");
    }

    private String parseJwt(HttpServletRequest request) {
        String headerAuth = request.getHeader("Authorization");
        if (StringUtils.hasText(headerAuth) && headerAuth.startsWith("Bearer ")) {
            return headerAuth.substring(7);
        }
        return null;
    }
}