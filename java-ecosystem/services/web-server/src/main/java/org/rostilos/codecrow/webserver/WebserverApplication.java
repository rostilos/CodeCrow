package org.rostilos.codecrow.webserver;

import org.springframework.boot.SpringApplication;
import org.springframework.boot.autoconfigure.SpringBootApplication;
import org.springframework.boot.autoconfigure.domain.EntityScan;
import org.springframework.context.annotation.ComponentScan;
import org.springframework.data.jpa.repository.config.EnableJpaAuditing;
import org.springframework.data.jpa.repository.config.EnableJpaRepositories;
import org.springframework.scheduling.annotation.EnableAsync;
import org.springframework.scheduling.annotation.EnableScheduling;

@SpringBootApplication(scanBasePackages = "org.rostilos.codecrow.webserver")
@EntityScan(basePackages = {
        "org.rostilos.codecrow.core.model",
        "org.rostilos.codecrow.commitgraph.model",
        "org.rostilos.codecrow.filecontent.model"
})
@ComponentScan(basePackages = {
        "org.rostilos.codecrow.webserver",
        "org.rostilos.codecrow.vcsclient",
        "org.rostilos.codecrow.core",
        "org.rostilos.codecrow.commitgraph",
        "org.rostilos.codecrow.filecontent",
        "org.rostilos.codecrow.security.jwt.utils",
        "org.rostilos.codecrow.security.web",
        "org.rostilos.codecrow.security.service",
        "org.rostilos.codecrow.security.oauth",
        "org.rostilos.codecrow.taskmanagement",

})
@EnableJpaRepositories(basePackages = {
        "org.rostilos.codecrow.core.persistence.repository",
        "org.rostilos.codecrow.commitgraph.persistence",
        "org.rostilos.codecrow.filecontent.persistence"
})
@EnableJpaAuditing
@EnableAsync
@EnableScheduling
public class WebserverApplication {
    public static void main(String[] args) {
        SpringApplication.run(WebserverApplication.class, args);
    }
}
