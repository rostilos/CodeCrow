package org.rostilos.codecrow.core.service;

import org.junit.jupiter.api.Test;
import org.rostilos.codecrow.core.persistence.repository.admin.SiteSettingsRepository;
import org.rostilos.codecrow.core.security.TokenEncryptionService;
import org.springframework.context.annotation.AnnotationConfigApplicationContext;
import org.springframework.test.context.support.TestPropertySourceUtils;
import static org.assertj.core.api.Assertions.assertThat;
import static org.mockito.Mockito.mock;

class SmtpPropertyCompatibilityTest {
    private AnnotationConfigApplicationContext context(String... properties) {
        var context = new AnnotationConfigApplicationContext();
        TestPropertySourceUtils.addInlinedPropertiesToEnvironment(context, properties);
        context.registerBean(SiteSettingsRepository.class, () -> mock(SiteSettingsRepository.class));
        context.registerBean(TokenEncryptionService.class, () -> mock(TokenEncryptionService.class));
        context.register(SiteSettingsProvider.class);
        context.refresh();
        return context;
    }

    @Test void configuredEmailPropertiesEnableTheSharedSmtpProvider() {
        try (var context = context("codecrow.email.enabled=true", "codecrow.email.from=billing@example.test",
                "codecrow.email.from-name=Billing", "spring.mail.host=smtp.example.test",
                "spring.mail.properties.mail.smtp.starttls.enable=false")) {
            var smtp = context.getBean(SiteSettingsProvider.class).getSmtpSettings();
            assertThat(smtp.enabled()).isTrue();
            assertThat(smtp.fromAddress()).isEqualTo("billing@example.test");
            assertThat(smtp.fromName()).isEqualTo("Billing");
            assertThat(smtp.host()).isEqualTo("smtp.example.test");
            assertThat(smtp.starttls()).isFalse();
        }
    }

    @Test void explicitSmtpDisableWinsOverLegacyEnable() {
        try (var context = context("codecrow.email.enabled=true", "codecrow.smtp.enabled=false",
                "codecrow.email.from=legacy@example.test", "codecrow.smtp.from-address=current@example.test")) {
            var smtp = context.getBean(SiteSettingsProvider.class).getSmtpSettings();
            assertThat(smtp.enabled()).isFalse();
            assertThat(smtp.fromAddress()).isEqualTo("current@example.test");
        }
    }

    @Test void unconfiguredEmailRemainsDisabled() {
        try (var context = context()) {
            assertThat(context.getBean(SiteSettingsProvider.class).getSmtpSettings().enabled()).isFalse();
        }
    }
}
