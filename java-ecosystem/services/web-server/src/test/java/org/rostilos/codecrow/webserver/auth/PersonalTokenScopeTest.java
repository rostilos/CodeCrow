package org.rostilos.codecrow.webserver.auth;

import org.junit.jupiter.api.Test;
import org.rostilos.codecrow.core.model.user.PersonalAccessToken;
import org.rostilos.codecrow.core.model.workspace.Workspace;
import org.springframework.mock.web.MockHttpServletRequest;
import org.springframework.mock.web.MockHttpServletResponse;
import org.springframework.web.servlet.HandlerMapping;
import java.util.Map;
import static org.assertj.core.api.Assertions.assertThat;

class PersonalTokenScopeTest {
    @Test
    void matchedWorkspaceAndMethodAreEnforcedAndTokensCannotMintTokens() throws Exception {
        var interceptor = new PersonalTokenScopeConfiguration();
        var token = new PersonalAccessToken();
        token.setWorkspace(new Workspace("workspace", "Team", ""));
        var request = new MockHttpServletRequest("GET", "/api/workspace/another-team/members");
        request.setAttribute("personalAccessToken", token);
        request.setAttribute(HandlerMapping.URI_TEMPLATE_VARIABLES_ATTRIBUTE, Map.of("workspaceSlug", "another-team"));
        assertThat(interceptor.preHandle(request, new MockHttpServletResponse(), new Object())).isFalse();
        request.setAttribute(HandlerMapping.URI_TEMPLATE_VARIABLES_ATTRIBUTE, Map.of("workspaceSlug", "workspace"));
        assertThat(interceptor.preHandle(request, new MockHttpServletResponse(), new Object())).isTrue();
        request.setMethod("PUT");
        assertThat(interceptor.preHandle(request, new MockHttpServletResponse(), new Object())).isFalse();
        token.setWriteAllowed(true);
        assertThat(interceptor.preHandle(request, new MockHttpServletResponse(), new Object())).isTrue();
        request.setAttribute(HandlerMapping.BEST_MATCHING_PATTERN_ATTRIBUTE, "/api/{workspaceSlug}/developer-tokens");
        assertThat(interceptor.preHandle(request, new MockHttpServletResponse(), new Object())).isFalse();
        request.removeAttribute(HandlerMapping.URI_TEMPLATE_VARIABLES_ATTRIBUTE);
        assertThat(interceptor.preHandle(request, new MockHttpServletResponse(), new Object())).isFalse();
    }
}
