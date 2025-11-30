module org.rostilos.codecrow.webserver {
    requires spring.boot.autoconfigure;
    requires spring.boot;
    requires org.rostilos.codecrow.vcs;
    requires org.rostilos.codecrow.core;
    requires okhttp3;
    requires spring.tx;
    requires spring.context;
    requires spring.web;
    requires spring.security.core;
    requires jakarta.validation;
    requires com.fasterxml.jackson.annotation;
    requires jjwt.api;
    requires jakarta.persistence;
    requires spring.beans;
    requires spring.security.crypto;
    requires spring.security.config;
    requires spring.security.web;
    requires org.rostilos.codecrow.security;
    requires com.fasterxml.jackson.databind;
    requires org.slf4j;
}