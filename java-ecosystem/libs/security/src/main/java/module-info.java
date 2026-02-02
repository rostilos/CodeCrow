module org.rostilos.codecrow.security {
    requires org.rostilos.codecrow.core;
    requires spring.security.core;
    requires spring.context;
    requires com.fasterxml.jackson.annotation;
    requires spring.security.config;
    requires spring.security.crypto;
    requires spring.security.web;
    requires spring.beans;
    requires org.apache.tomcat.embed.core;
    requires org.slf4j;
    requires spring.web;
    requires com.fasterxml.jackson.databind;
    requires jjwt.api;
    requires spring.core;
    exports org.rostilos.codecrow.security.oauth;
    exports org.rostilos.codecrow.security.service;
    exports org.rostilos.codecrow.security.web.jwt;
    exports org.rostilos.codecrow.security.jwt.utils;
    exports org.rostilos.codecrow.security.pipelineagent;
    exports org.rostilos.codecrow.security.pipelineagent.jwt;
    exports org.rostilos.codecrow.security.annotations;
}
