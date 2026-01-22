package org.rostilos.codecrow.email.config;

import org.junit.jupiter.api.Test;
import org.springframework.boot.autoconfigure.AutoConfiguration;
import org.springframework.boot.context.properties.EnableConfigurationProperties;
import org.springframework.context.annotation.ComponentScan;

import static org.assertj.core.api.Assertions.assertThat;

class EmailAutoConfigurationTest {

    @Test
    void testClassIsAnnotatedWithAutoConfiguration() {
        assertThat(EmailAutoConfiguration.class.isAnnotationPresent(AutoConfiguration.class)).isTrue();
    }

    @Test
    void testClassEnablesEmailProperties() {
        EnableConfigurationProperties annotation = 
            EmailAutoConfiguration.class.getAnnotation(EnableConfigurationProperties.class);
        
        assertThat(annotation).isNotNull();
        assertThat(annotation.value()).contains(EmailProperties.class);
    }

    @Test
    void testClassScansEmailPackage() {
        ComponentScan annotation = EmailAutoConfiguration.class.getAnnotation(ComponentScan.class);
        
        assertThat(annotation).isNotNull();
        assertThat(annotation.basePackages()).contains("org.rostilos.codecrow.email");
    }

    @Test
    void testClassCanBeInstantiated() {
        EmailAutoConfiguration config = new EmailAutoConfiguration();
        
        assertThat(config).isNotNull();
    }
}
