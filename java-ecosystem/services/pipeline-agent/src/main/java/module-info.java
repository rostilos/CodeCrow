module org.rostilos.codecrow.pipelineagent {
    requires spring.boot;
    requires spring.boot.autoconfigure;
    requires org.rostilos.codecrow.vcs;
    requires org.rostilos.codecrow.core;
    requires org.rostilos.codecrow.analysisengine;
    requires org.rostilos.codecrow.ragengine;
    requires spring.beans;
    requires org.slf4j;
    requires jakarta.validation;
    requires spring.web;
    requires jjwt.api;
    requires okhttp3;
    requires com.fasterxml.jackson.annotation;
    requires com.fasterxml.jackson.databind;
    requires spring.data.jpa;
    requires spring.security.config;
    requires spring.context;
    requires spring.core;
    requires spring.security.core;
    requires spring.security.crypto;
    requires spring.security.web;
    requires org.apache.tomcat.embed.core;
    requires org.aspectj.weaver;
    requires org.rostilos.codecrow.security;
    requires spring.tx;
    requires spring.webmvc;
}
