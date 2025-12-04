package org.rostilos.codecrow.email.service;

import org.rostilos.codecrow.email.config.EmailProperties;
import org.slf4j.Logger;
import org.slf4j.LoggerFactory;
import org.springframework.stereotype.Service;
import org.thymeleaf.TemplateEngine;
import org.thymeleaf.context.Context;

import java.time.Year;
import java.util.Arrays;
import java.util.HashMap;
import java.util.Map;

@Service
public class EmailTemplateService {
    
    private static final Logger logger = LoggerFactory.getLogger(EmailTemplateService.class);
    
    private final TemplateEngine emailTemplateEngine;
    private final EmailProperties emailProperties;
    
    public EmailTemplateService(TemplateEngine emailTemplateEngine, EmailProperties emailProperties) {
        this.emailTemplateEngine = emailTemplateEngine;
        this.emailProperties = emailProperties;
    }
    
    public String renderTemplate(String templateName, Map<String, Object> variables) {
        Context context = new Context();
        
        context.setVariable("appName", emailProperties.getAppName());
        context.setVariable("frontendUrl", emailProperties.getFrontendUrl());
        context.setVariable("currentYear", Year.now().getValue());
        
        // Add custom variables
        if (variables != null) {
            variables.forEach(context::setVariable);
        }
        
        try {
            return emailTemplateEngine.process(templateName, context);
        } catch (Exception e) {
            logger.error("Failed to render email template: {}", templateName, e);
            throw new RuntimeException("Failed to render email template: " + templateName, e);
        }
    }
    
    public String getTwoFactorCodeTemplate(String code, int expirationMinutes) {
        Map<String, Object> variables = new HashMap<>();
        variables.put("code", code);
        variables.put("expirationMinutes", expirationMinutes);
        return renderTemplate("two-factor-code", variables);
    }
    
    public String getTwoFactorEnabledTemplate(String twoFactorType) {
        String methodName = "TOTP".equals(twoFactorType) 
            ? "Google Authenticator (TOTP)" 
            : "Email verification";
        
        Map<String, Object> variables = new HashMap<>();
        variables.put("methodName", methodName);
        return renderTemplate("two-factor-enabled", variables);
    }
    
    public String getTwoFactorDisabledTemplate() {
        return renderTemplate("two-factor-disabled", null);
    }
    
    public String getBackupCodesTemplate(String[] backupCodes) {
        Map<String, Object> variables = new HashMap<>();
        variables.put("backupCodes", Arrays.asList(backupCodes));
        return renderTemplate("backup-codes", variables);
    }
}
