package org.rostilos.codecrow.email.config;

import org.junit.jupiter.api.Test;

import static org.assertj.core.api.Assertions.assertThat;

class EmailPropertiesTest {

    @Test
    void testDefaultValues() {
        EmailProperties properties = new EmailProperties();

        assertThat(properties.isEnabled()).isTrue();
        assertThat(properties.getFrom()).isEqualTo("noreply@codecrow.io");
        assertThat(properties.getFromName()).isEqualTo("CodeCrow");
        assertThat(properties.getFrontendUrl()).isEqualTo("http://localhost:8080");
        assertThat(properties.getAppName()).isEqualTo("CodeCrow");
    }

    @Test
    void testSetEnabled() {
        EmailProperties properties = new EmailProperties();
        
        properties.setEnabled(false);
        
        assertThat(properties.isEnabled()).isFalse();
    }

    @Test
    void testSetFrom() {
        EmailProperties properties = new EmailProperties();
        
        properties.setFrom("test@example.com");
        
        assertThat(properties.getFrom()).isEqualTo("test@example.com");
    }

    @Test
    void testSetFromName() {
        EmailProperties properties = new EmailProperties();
        
        properties.setFromName("Test Sender");
        
        assertThat(properties.getFromName()).isEqualTo("Test Sender");
    }

    @Test
    void testSetFrontendUrl() {
        EmailProperties properties = new EmailProperties();
        
        properties.setFrontendUrl("https://example.com");
        
        assertThat(properties.getFrontendUrl()).isEqualTo("https://example.com");
    }

    @Test
    void testSetAppName() {
        EmailProperties properties = new EmailProperties();
        
        properties.setAppName("MyApp");
        
        assertThat(properties.getAppName()).isEqualTo("MyApp");
    }

    @Test
    void testAllPropertiesCanBeSet() {
        EmailProperties properties = new EmailProperties();
        
        properties.setEnabled(false);
        properties.setFrom("custom@example.com");
        properties.setFromName("Custom Sender");
        properties.setFrontendUrl("https://custom.com");
        properties.setAppName("CustomApp");
        
        assertThat(properties.isEnabled()).isFalse();
        assertThat(properties.getFrom()).isEqualTo("custom@example.com");
        assertThat(properties.getFromName()).isEqualTo("Custom Sender");
        assertThat(properties.getFrontendUrl()).isEqualTo("https://custom.com");
        assertThat(properties.getAppName()).isEqualTo("CustomApp");
    }
}
