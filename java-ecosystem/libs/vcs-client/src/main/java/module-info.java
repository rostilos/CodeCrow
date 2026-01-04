module org.rostilos.codecrow.vcs {
    requires org.rostilos.codecrow.core;
    requires okhttp3;
    requires com.fasterxml.jackson.databind;
    requires java.net.http;
    requires spring.context;
    requires org.slf4j;
    requires spring.beans;
    requires annotations;
    requires com.fasterxml.jackson.annotation;
    requires com.fasterxml.jackson.core;
    requires org.rostilos.codecrow.security;
    requires spring.web;
    requires spring.tx;
    requires jjwt.api;

    exports org.rostilos.codecrow.vcsclient;
    exports org.rostilos.codecrow.vcsclient.model;
    exports org.rostilos.codecrow.vcsclient.bitbucket.cloud;
    exports org.rostilos.codecrow.vcsclient.bitbucket.cloud.actions;
    exports org.rostilos.codecrow.vcsclient.bitbucket.cloud.dto.response;
    exports org.rostilos.codecrow.vcsclient.bitbucket.service;
    exports org.rostilos.codecrow.vcsclient.bitbucket.model.report;
    exports org.rostilos.codecrow.vcsclient.github;
    exports org.rostilos.codecrow.vcsclient.github.actions;
    exports org.rostilos.codecrow.vcsclient.github.dto.response;
    exports org.rostilos.codecrow.vcsclient.gitlab;
    exports org.rostilos.codecrow.vcsclient.gitlab.actions;
    exports org.rostilos.codecrow.vcsclient.gitlab.dto.response;
}