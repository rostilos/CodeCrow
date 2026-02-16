package org.rostilos.codecrow.webserver.admin.config;

import org.rostilos.codecrow.core.dto.admin.BaseUrlSettingsDTO;
import org.rostilos.codecrow.core.dto.admin.SmtpSettingsDTO;
import org.rostilos.codecrow.email.config.EmailProperties;
import org.rostilos.codecrow.core.service.SiteSettingsProvider;
import org.springframework.context.annotation.Primary;
import org.springframework.stereotype.Component;

/**
 * Dynamic override of EmailProperties that reads from site settings (DB)
 * instead of application.properties.
 * <p>
 * Falls back to defaults if values are not configured in DB.
 */
@Primary
@Component
public class DynamicEmailProperties extends EmailProperties {

    private final SiteSettingsProvider siteSettingsProvider;

    public DynamicEmailProperties(SiteSettingsProvider siteSettingsProvider) {
        this.siteSettingsProvider = siteSettingsProvider;
    }

    @Override
    public boolean isEnabled() {
        try {
            SmtpSettingsDTO smtp = siteSettingsProvider.getSmtpSettings();
            return smtp.enabled();
        } catch (Exception e) {
            return super.isEnabled();
        }
    }

    @Override
    public String getFrom() {
        try {
            SmtpSettingsDTO smtp = siteSettingsProvider.getSmtpSettings();
            return (smtp.fromAddress() != null && !smtp.fromAddress().isBlank())
                    ? smtp.fromAddress() : super.getFrom();
        } catch (Exception e) {
            return super.getFrom();
        }
    }

    @Override
    public String getFromName() {
        try {
            SmtpSettingsDTO smtp = siteSettingsProvider.getSmtpSettings();
            return (smtp.fromName() != null && !smtp.fromName().isBlank())
                    ? smtp.fromName() : super.getFromName();
        } catch (Exception e) {
            return super.getFromName();
        }
    }

    @Override
    public String getFrontendUrl() {
        try {
            BaseUrlSettingsDTO urls = siteSettingsProvider.getBaseUrlSettings();
            return (urls.frontendUrl() != null && !urls.frontendUrl().isBlank())
                    ? urls.frontendUrl() : super.getFrontendUrl();
        } catch (Exception e) {
            return super.getFrontendUrl();
        }
    }
}
