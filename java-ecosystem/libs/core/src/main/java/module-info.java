module org.rostilos.codecrow.core {
    requires jakarta.persistence;
    requires jakarta.validation;
    requires spring.data.jpa;
    requires spring.context;
    requires org.hibernate.orm.core;
    requires spring.data.commons;
    requires com.fasterxml.jackson.databind;
    requires spring.beans;
    requires spring.security.core;
    requires spring.security.config;
    requires spring.tx;
    requires spring.security.crypto;
    requires org.slf4j;
    requires spring.core;
    requires okhttp3;
    requires spring.security.web;
    requires java.net.http;
    requires spring.web;
    requires org.apache.tomcat.embed.core;
    requires jjwt.api;
    requires com.fasterxml.jackson.annotation;

    // Export AI and Project packages for web-server to access entities and repositories
    exports org.rostilos.codecrow.core.model.ai;
    exports org.rostilos.codecrow.core.persistence.repository.ai;
    
    // Keep existing exports
    exports org.rostilos.codecrow.core.model.vcs;
    exports org.rostilos.codecrow.core.model.user;
    exports org.rostilos.codecrow.core.model.project;
    exports org.rostilos.codecrow.core.model;
    exports org.rostilos.codecrow.core.model.user.account_type;
    exports org.rostilos.codecrow.core.model.user.status;
    exports org.rostilos.codecrow.core.persistence.repository;
    exports org.rostilos.codecrow.core.persistence.repository.user;
    exports org.rostilos.codecrow.core.persistence.repository.project;

    // Open entities for JPA reflection access (Hibernate) if using strong encapsulation
    opens org.rostilos.codecrow.core.model.ai to spring.core, spring.beans, spring.context, org.hibernate.orm.core;
    opens org.rostilos.codecrow.core.model.project to spring.core, spring.beans, spring.context, org.hibernate.orm.core, org.rostilos.codecrow.analysisengine;
    // Open repositories package if Spring needs to proxy/reﬂectively access
    opens org.rostilos.codecrow.core.persistence.repository.ai to spring.core, spring.beans, spring.context;
    exports org.rostilos.codecrow.core.persistence.repository.vcs;
    exports org.rostilos.codecrow.core.model.vcs.config.github;
    exports org.rostilos.codecrow.core.model.vcs.config.gitlab;
    exports org.rostilos.codecrow.core.model.vcs.config.cloud;
    exports org.rostilos.codecrow.core.model.vcs.config;
    exports org.rostilos.codecrow.core.dto.bitbucket;
    exports org.rostilos.codecrow.core.dto.github;
    exports org.rostilos.codecrow.core.dto.gitlab;
    exports org.rostilos.codecrow.core.dto.ai;
    exports org.rostilos.codecrow.core.dto.user;
    exports org.rostilos.codecrow.core.dto.project;
    exports org.rostilos.codecrow.core.persistence.repository.codeanalysis;
    exports org.rostilos.codecrow.core.model.codeanalysis;
    exports org.rostilos.codecrow.core.service;
    exports org.rostilos.codecrow.core.model.workspace;
    opens org.rostilos.codecrow.core.model.workspace to org.hibernate.orm.core, spring.beans, spring.context, spring.core;
    exports org.rostilos.codecrow.core.persistence.repository.workspace;
    exports org.rostilos.codecrow.core.dto.workspace;
    exports org.rostilos.codecrow.core.model.pullrequest;
    exports org.rostilos.codecrow.core.dto.analysis;
    exports org.rostilos.codecrow.core.dto.analysis.issue;
    exports org.rostilos.codecrow.core.persistence.repository.pullrequest;
    exports org.rostilos.codecrow.core.dto.pullrequest;
    exports org.rostilos.codecrow.core.utils;
    exports org.rostilos.codecrow.core.model.branch;
    exports org.rostilos.codecrow.core.persistence.repository.branch;
    exports org.rostilos.codecrow.core.model.project.config;
    exports org.rostilos.codecrow.core.model.analysis;
    exports org.rostilos.codecrow.core.persistence.repository.analysis;
    opens org.rostilos.codecrow.core.model.branch to org.hibernate.orm.core, spring.beans, spring.context, spring.core;
    opens org.rostilos.codecrow.core.model.pullrequest to org.hibernate.orm.core, spring.beans, spring.context, spring.core, org.rostilos.codecrow.analysisengine;
    opens org.rostilos.codecrow.core.model.analysis to org.hibernate.orm.core, spring.beans, spring.context, spring.core, org.rostilos.codecrow.analysisengine;
    exports org.rostilos.codecrow.core.util;
    exports org.rostilos.codecrow.core.model.user.twofactor;
    opens org.rostilos.codecrow.core.model.user.twofactor to org.hibernate.orm.core, spring.beans, spring.context, spring.core;
    exports org.rostilos.codecrow.core.model.job;
    exports org.rostilos.codecrow.core.persistence.repository.job;
    exports org.rostilos.codecrow.core.dto.job;
    opens org.rostilos.codecrow.core.model.job to org.hibernate.orm.core, spring.beans, spring.context, spring.core;
    exports org.rostilos.codecrow.core.model.qualitygate;
    exports org.rostilos.codecrow.core.dto.qualitygate;
    exports org.rostilos.codecrow.core.persistence.repository.qualitygate;
    exports org.rostilos.codecrow.core.service.qualitygate;
    
    // RAG delta index exports
    exports org.rostilos.codecrow.core.model.rag;
    exports org.rostilos.codecrow.core.persistence.repository.rag;
    opens org.rostilos.codecrow.core.model.rag to org.hibernate.orm.core, spring.beans, spring.context, spring.core;
}
