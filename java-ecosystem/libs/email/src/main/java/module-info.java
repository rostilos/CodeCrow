module org.rostilos.codecrow.email {
    requires spring.context;
    requires spring.boot;
    requires spring.boot.autoconfigure;
    requires spring.context.support;
    requires spring.core;
    requires spring.beans;
    requires jakarta.mail;
    requires org.slf4j;
    requires thymeleaf;
    requires thymeleaf.spring6;
    
    exports org.rostilos.codecrow.email.service;
    exports org.rostilos.codecrow.email.config;
    
    opens org.rostilos.codecrow.email.config to spring.core, spring.beans, spring.context;
    opens org.rostilos.codecrow.email.service to spring.core, spring.beans, spring.context;
}
