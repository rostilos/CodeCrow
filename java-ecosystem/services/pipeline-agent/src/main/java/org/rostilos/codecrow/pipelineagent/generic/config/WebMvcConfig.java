package org.rostilos.codecrow.pipelineagent.generic.config;

import org.springframework.beans.factory.annotation.Qualifier;
import org.springframework.beans.factory.annotation.Value;
import org.springframework.boot.convert.DurationStyle;
import org.springframework.context.annotation.Configuration;
import org.springframework.core.task.AsyncTaskExecutor;
import org.springframework.http.MediaType;
import org.springframework.http.converter.HttpMessageConverter;
import org.springframework.http.converter.json.MappingJackson2HttpMessageConverter;
import org.springframework.security.task.DelegatingSecurityContextAsyncTaskExecutor;
import org.springframework.web.servlet.config.annotation.AsyncSupportConfigurer;
import org.springframework.web.servlet.config.annotation.WebMvcConfigurer;

import java.util.ArrayList;
import java.util.List;

@Configuration
public class WebMvcConfig implements WebMvcConfigurer {
    private final AsyncTaskExecutor streamExecutor;
    private final long requestTimeoutMillis;

    public WebMvcConfig(
            @Qualifier("webMvcAsyncExecutor") AsyncTaskExecutor streamExecutor,
            @Value("${spring.mvc.async.request-timeout:-1}") String requestTimeout) {
        this.streamExecutor = streamExecutor;
        this.requestTimeoutMillis = DurationStyle.detectAndParse(requestTimeout).toMillis();
    }

    @Override
    public void extendMessageConverters(List<HttpMessageConverter<?>> converters) {
        for (HttpMessageConverter<?> converter : converters) {
            if (converter instanceof MappingJackson2HttpMessageConverter jackson) {
                List<MediaType> mediaTypes = new ArrayList<>(jackson.getSupportedMediaTypes());
                mediaTypes.add(MediaType.parseMediaType("application/x-ndjson"));
                jackson.setSupportedMediaTypes(mediaTypes);
            }
        }
    }

    @Override
    public void configureAsyncSupport(AsyncSupportConfigurer configurer) {
        configurer.setDefaultTimeout(requestTimeoutMillis);
        // Propagate the authenticated request context without giving stream
        // writers ownership of review or repository-maintenance workers.
        AsyncTaskExecutor securityExecutor = new DelegatingSecurityContextAsyncTaskExecutor(streamExecutor);
        configurer.setTaskExecutor(securityExecutor);
    }
}

