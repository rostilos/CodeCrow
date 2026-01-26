package org.rostilos.codecrow.notification.config;

import org.springframework.boot.autoconfigure.AutoConfiguration;
import org.springframework.boot.autoconfigure.domain.EntityScan;
import org.springframework.boot.context.properties.EnableConfigurationProperties;
import org.springframework.context.annotation.ComponentScan;
import org.springframework.data.jpa.repository.config.EnableJpaRepositories;
import org.springframework.scheduling.annotation.EnableAsync;
import org.springframework.scheduling.annotation.EnableScheduling;

/**
 * Auto-configuration for the notification module.
 */
@AutoConfiguration
@ComponentScan(basePackages = "org.rostilos.codecrow.notification")
@EntityScan(basePackages = "org.rostilos.codecrow.notification.model")
@EnableJpaRepositories(basePackages = "org.rostilos.codecrow.notification.repository")
@EnableConfigurationProperties(NotificationProperties.class)
@EnableAsync
@EnableScheduling
public class NotificationAutoConfiguration {
}
