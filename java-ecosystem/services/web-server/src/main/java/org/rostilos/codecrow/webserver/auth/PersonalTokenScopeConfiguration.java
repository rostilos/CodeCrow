package org.rostilos.codecrow.webserver.auth;

import jakarta.servlet.http.HttpServletRequest;
import jakarta.servlet.http.HttpServletResponse;
import org.rostilos.codecrow.core.model.user.PersonalAccessToken;
import org.rostilos.codecrow.core.service.PersonalAccessTokenService;
import org.springframework.context.annotation.Configuration;
import org.springframework.web.servlet.HandlerInterceptor;
import org.springframework.web.servlet.HandlerMapping;
import org.springframework.web.servlet.config.annotation.InterceptorRegistry;
import org.springframework.web.servlet.config.annotation.WebMvcConfigurer;
import java.util.Map;

@Configuration
public class PersonalTokenScopeConfiguration implements WebMvcConfigurer, HandlerInterceptor {
    @Override
    public void addInterceptors(InterceptorRegistry registry) { registry.addInterceptor(this); }

    @Override
    public boolean preHandle(HttpServletRequest request, HttpServletResponse response, Object handler) throws Exception {
        if (!(request.getAttribute("personalAccessToken") instanceof PersonalAccessToken token)) return true;
        // Use the matched route, so reserved workspace names cannot impersonate another API prefix.
        var variables = request.getAttribute(HandlerMapping.URI_TEMPLATE_VARIABLES_ATTRIBUTE);
        String workspace = variables instanceof Map<?, ?> values ? (String) values.get("workspaceSlug") : null;
        String pattern = String.valueOf(request.getAttribute(HandlerMapping.BEST_MATCHING_PATTERN_ATTRIBUTE));
        if (!PersonalAccessTokenService.permits(token, request.getMethod(), workspace) || pattern.contains("/developer-tokens")) {
            response.setStatus(HttpServletResponse.SC_FORBIDDEN);
            response.setContentType("application/json");
            response.getWriter().write("{\"message\":\"Token scope does not allow this operation.\",\"status\":403}");
            return false;
        }
        return true;
    }
}
