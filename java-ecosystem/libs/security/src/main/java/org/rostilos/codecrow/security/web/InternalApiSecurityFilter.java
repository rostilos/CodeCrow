package org.rostilos.codecrow.security.web;

import jakarta.servlet.FilterChain;
import jakarta.servlet.ServletException;
import jakarta.servlet.http.HttpServletRequest;
import jakarta.servlet.http.HttpServletResponse;
import org.slf4j.Logger;
import org.slf4j.LoggerFactory;
import org.springframework.beans.factory.annotation.Value;
import org.springframework.core.Ordered;
import org.springframework.core.annotation.Order;
import org.springframework.stereotype.Component;
import org.springframework.web.filter.OncePerRequestFilter;

import java.io.IOException;

/**
 * Security filter for internal API endpoints.
 * Validates X-Internal-Secret header for /api/internal/** requests.
 */
@Component
@Order(Ordered.HIGHEST_PRECEDENCE)
public class InternalApiSecurityFilter extends OncePerRequestFilter {

    private static final Logger log = LoggerFactory.getLogger(InternalApiSecurityFilter.class);
    private static final String INTERNAL_SECRET_HEADER = "X-Internal-Secret";
    private static final String INTERNAL_API_PATH = "/api/internal/";

    @Value("${codecrow.internal.api.secret:}")
    private String internalApiSecret;

    @Override
    protected void doFilterInternal(HttpServletRequest request, HttpServletResponse response, 
                                    FilterChain filterChain) throws ServletException, IOException {
        String requestUri = request.getRequestURI();
        
        if (requestUri.startsWith(INTERNAL_API_PATH)) {
            if (!validateInternalSecret(request, response)) {
                return;
            }
        }
        
        filterChain.doFilter(request, response);
    }

    private boolean validateInternalSecret(HttpServletRequest request, HttpServletResponse response) 
            throws IOException {
        if (internalApiSecret == null || internalApiSecret.isBlank()) {
            log.warn("Internal API secret not configured - denying access to {}", request.getRequestURI());
            response.setStatus(HttpServletResponse.SC_FORBIDDEN);
            response.setContentType("application/json");
            response.getWriter().write("{\"error\": \"Internal API not available\"}");
            return false;
        }

        String providedSecret = request.getHeader(INTERNAL_SECRET_HEADER);
        
        if (providedSecret == null || providedSecret.isBlank()) {
            log.warn("Missing {} header for internal API call to {}", INTERNAL_SECRET_HEADER, request.getRequestURI());
            response.setStatus(HttpServletResponse.SC_UNAUTHORIZED);
            response.setContentType("application/json");
            response.getWriter().write("{\"error\": \"Missing authentication\"}");
            return false;
        }

        if (!internalApiSecret.equals(providedSecret)) {
            log.warn("Invalid {} header for internal API call to {}", INTERNAL_SECRET_HEADER, request.getRequestURI());
            response.setStatus(HttpServletResponse.SC_UNAUTHORIZED);
            response.setContentType("application/json");
            response.getWriter().write("{\"error\": \"Invalid authentication\"}");
            return false;
        }

        log.debug("Internal API access granted for {}", request.getRequestURI());
        return true;
    }
}
