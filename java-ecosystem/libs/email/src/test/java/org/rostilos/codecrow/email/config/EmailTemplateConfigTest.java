package org.rostilos.codecrow.email.config;

import org.junit.jupiter.api.Test;
import org.thymeleaf.TemplateEngine;

import static org.assertj.core.api.Assertions.assertThat;

class EmailTemplateConfigTest {

    @Test
    void testEmailTemplateEngineCreation() {
        EmailTemplateConfig config = new EmailTemplateConfig();
        
        TemplateEngine engine = config.emailTemplateEngine();
        
        assertThat(engine).isNotNull();
        assertThat(engine.getTemplateResolvers()).isNotEmpty();
    }

    @Test
    void testTemplateEngineIsSpringTemplateEngine() {
        EmailTemplateConfig config = new EmailTemplateConfig();
        
        TemplateEngine engine = config.emailTemplateEngine();
        
        assertThat(engine).isInstanceOf(org.thymeleaf.spring6.SpringTemplateEngine.class);
    }

    @Test
    void testMultipleCallsCreateNewInstances() {
        EmailTemplateConfig config = new EmailTemplateConfig();
        
        TemplateEngine engine1 = config.emailTemplateEngine();
        TemplateEngine engine2 = config.emailTemplateEngine();
        
        assertThat(engine1).isNotSameAs(engine2);
    }
}
