package org.rostilos.codecrow.email.config;

import org.springframework.boot.autoconfigure.AutoConfiguration;
import org.springframework.boot.context.properties.EnableConfigurationProperties;
import org.springframework.context.annotation.ComponentScan;

@AutoConfiguration
@EnableConfigurationProperties(EmailProperties.class)
@ComponentScan(basePackages = "org.rostilos.codecrow.email")
public class EmailAutoConfiguration {
}
