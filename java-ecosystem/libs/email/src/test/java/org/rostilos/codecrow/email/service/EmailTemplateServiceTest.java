package org.rostilos.codecrow.email.service;

import org.junit.jupiter.api.BeforeEach;
import org.junit.jupiter.api.Test;
import org.junit.jupiter.api.extension.ExtendWith;
import org.mockito.Mock;
import org.mockito.junit.jupiter.MockitoExtension;
import org.rostilos.codecrow.email.config.EmailProperties;
import org.thymeleaf.TemplateEngine;
import org.thymeleaf.context.Context;

import java.util.HashMap;
import java.util.Map;

import static org.assertj.core.api.Assertions.assertThat;
import static org.assertj.core.api.Assertions.assertThatThrownBy;
import static org.mockito.ArgumentMatchers.any;
import static org.mockito.ArgumentMatchers.eq;
import static org.mockito.Mockito.verify;
import static org.mockito.Mockito.when;

@ExtendWith(MockitoExtension.class)
class EmailTemplateServiceTest {

    @Mock
    private TemplateEngine templateEngine;

    @Mock
    private EmailProperties emailProperties;

    private EmailTemplateService service;

    @BeforeEach
    void setUp() {
        when(emailProperties.getAppName()).thenReturn("TestApp");
        when(emailProperties.getFrontendUrl()).thenReturn("https://test.com");
        
        service = new EmailTemplateService(templateEngine, emailProperties);
    }

    @Test
    void testRenderTemplate_Success() {
        Map<String, Object> variables = new HashMap<>();
        variables.put("testKey", "testValue");
        when(templateEngine.process(eq("test-template"), any(Context.class)))
            .thenReturn("<html>Test Content</html>");

        String result = service.renderTemplate("test-template", variables);

        assertThat(result).isEqualTo("<html>Test Content</html>");
        verify(templateEngine).process(eq("test-template"), any(Context.class));
    }

    @Test
    void testRenderTemplate_WithNullVariables() {
        when(templateEngine.process(eq("test-template"), any(Context.class)))
            .thenReturn("<html>Content</html>");

        String result = service.renderTemplate("test-template", null);

        assertThat(result).isEqualTo("<html>Content</html>");
    }

    @Test
    void testRenderTemplate_ThrowsException() {
        when(templateEngine.process(eq("invalid-template"), any(Context.class)))
            .thenThrow(new RuntimeException("Template not found"));

        assertThatThrownBy(() -> service.renderTemplate("invalid-template", null))
            .isInstanceOf(RuntimeException.class)
            .hasMessageContaining("Failed to render email template");
    }

    @Test
    void testGetTwoFactorCodeTemplate() {
        when(templateEngine.process(eq("two-factor-code"), any(Context.class)))
            .thenReturn("<html>Code: 123456</html>");

        String result = service.getTwoFactorCodeTemplate("123456", 10);

        assertThat(result).contains("123456");
        verify(templateEngine).process(eq("two-factor-code"), any(Context.class));
    }

    @Test
    void testGetTwoFactorEnabledTemplate_TOTP() {
        when(templateEngine.process(eq("two-factor-enabled"), any(Context.class)))
            .thenReturn("<html>Enabled</html>");

        String result = service.getTwoFactorEnabledTemplate("TOTP");

        assertThat(result).isNotNull();
        verify(templateEngine).process(eq("two-factor-enabled"), any(Context.class));
    }

    @Test
    void testGetTwoFactorEnabledTemplate_Email() {
        when(templateEngine.process(eq("two-factor-enabled"), any(Context.class)))
            .thenReturn("<html>Enabled</html>");

        String result = service.getTwoFactorEnabledTemplate("EMAIL");

        assertThat(result).isNotNull();
        verify(templateEngine).process(eq("two-factor-enabled"), any(Context.class));
    }

    @Test
    void testGetTwoFactorDisabledTemplate() {
        when(templateEngine.process(eq("two-factor-disabled"), any(Context.class)))
            .thenReturn("<html>Disabled</html>");

        String result = service.getTwoFactorDisabledTemplate();

        assertThat(result).isEqualTo("<html>Disabled</html>");
        verify(templateEngine).process(eq("two-factor-disabled"), any(Context.class));
    }

    @Test
    void testGetBackupCodesTemplate() {
        String[] codes = {"CODE1", "CODE2", "CODE3"};
        when(templateEngine.process(eq("backup-codes"), any(Context.class)))
            .thenReturn("<html>Backup codes</html>");

        String result = service.getBackupCodesTemplate(codes);

        assertThat(result).isNotNull();
        verify(templateEngine).process(eq("backup-codes"), any(Context.class));
    }

    @Test
    void testGetPasswordResetTemplate() {
        when(templateEngine.process(eq("password-reset"), any(Context.class)))
            .thenReturn("<html>Reset password</html>");

        String result = service.getPasswordResetTemplate("testuser", "https://test.com/reset");

        assertThat(result).isNotNull();
        verify(templateEngine).process(eq("password-reset"), any(Context.class));
    }

    @Test
    void testGetPasswordChangedTemplate() {
        when(templateEngine.process(eq("password-changed"), any(Context.class)))
            .thenReturn("<html>Password changed</html>");

        String result = service.getPasswordChangedTemplate("testuser");

        assertThat(result).isNotNull();
        verify(templateEngine).process(eq("password-changed"), any(Context.class));
    }

    @Test
    void testRenderTemplate_AddsCommonVariables() {
        when(templateEngine.process(eq("test"), any(Context.class))).thenReturn("<html>Test</html>");

        service.renderTemplate("test", null);

        verify(emailProperties).getAppName();
        verify(emailProperties).getFrontendUrl();
    }
}
