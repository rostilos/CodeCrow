package org.rostilos.codecrow.webserver.config;

import org.springframework.context.annotation.Configuration;
import org.springframework.core.task.AsyncTaskExecutor;
import org.springframework.scheduling.concurrent.ThreadPoolTaskExecutor;
import org.springframework.security.task.DelegatingSecurityContextAsyncTaskExecutor;
import org.springframework.web.servlet.config.annotation.AsyncSupportConfigurer;
import org.springframework.web.servlet.config.annotation.WebMvcConfigurer;

/**
 * Web MVC configuration for web-server module.
 * Configures async support with security context propagation for SSE endpoints.
 */
@Configuration
public class WebMvcConfig implements WebMvcConfigurer {

    @Override
    public void configureAsyncSupport(AsyncSupportConfigurer configurer) {
        configurer.setDefaultTimeout(30 * 60 * 1000L);
        
        ThreadPoolTaskExecutor executor = new ThreadPoolTaskExecutor();
        executor.setCorePoolSize(5);
        executor.setMaxPoolSize(10);
        executor.setQueueCapacity(50);
        executor.setThreadNamePrefix("webserver-async-");
        executor.initialize();

        // Wrap it in the Security Context executor
        // This ensures the SecurityContext is propagated to async threads
        // Required for SSE endpoints to maintain authentication during async dispatch
        AsyncTaskExecutor securityExecutor = new DelegatingSecurityContextAsyncTaskExecutor(executor);

        configurer.setTaskExecutor(securityExecutor);
    }
}
