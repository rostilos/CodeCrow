package org.rostilos.codecrow.webserver;

import org.springframework.boot.SpringApplication;
import org.springframework.boot.autoconfigure.SpringBootApplication;
import org.springframework.boot.autoconfigure.domain.EntityScan;
import org.springframework.context.annotation.ComponentScan;
import org.springframework.data.jpa.repository.config.EnableJpaAuditing;
import org.springframework.data.jpa.repository.config.EnableJpaRepositories;
import org.springframework.scheduling.annotation.EnableAsync;

@SpringBootApplication(scanBasePackages = "org.rostilos.codecrow.webserver")
@EntityScan(basePackages = {
        "org.rostilos.codecrow.core.model"
})
@ComponentScan(basePackages = {
        "org.rostilos.codecrow.webserver",
        "org.rostilos.codecrow.vcsclient",
        "org.rostilos.codecrow.core",
        "org.rostilos.codecrow.security.jwt.utils",
        "org.rostilos.codecrow.security.web",
        "org.rostilos.codecrow.security.service",
        "org.rostilos.codecrow.security.oauth",

})
@EnableJpaRepositories(basePackages = "org.rostilos.codecrow.core.persistence.repository")
@EnableJpaAuditing
@EnableAsync
public class WebserverApplication {
    public static void main(String[] args) {
        SpringApplication.run(WebserverApplication.class, args);
    }
}
