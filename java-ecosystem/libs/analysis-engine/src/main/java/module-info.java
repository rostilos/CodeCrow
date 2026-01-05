module org.rostilos.codecrow.analysisengine {
    requires spring.boot;
    requires spring.boot.autoconfigure;
    requires spring.beans;
    requires spring.context;
    requires spring.core;
    requires spring.web;
    requires spring.data.jpa;
    requires spring.tx;
    requires org.rostilos.codecrow.core;
    requires org.rostilos.codecrow.vcs;
    requires okhttp3;
    requires org.slf4j;
    requires jakarta.validation;
    requires com.fasterxml.jackson.databind;
    requires com.fasterxml.jackson.annotation;
    requires jakarta.persistence;
    requires kotlin.stdlib;

    exports org.rostilos.codecrow.analysisengine.aiclient;
    exports org.rostilos.codecrow.analysisengine.config;
    exports org.rostilos.codecrow.analysisengine.dto.request.ai;
    exports org.rostilos.codecrow.analysisengine.dto.request.processor;
    exports org.rostilos.codecrow.analysisengine.dto.request.validation;
    exports org.rostilos.codecrow.analysisengine.exception;
    exports org.rostilos.codecrow.analysisengine.processor;
    exports org.rostilos.codecrow.analysisengine.processor.analysis;
    exports org.rostilos.codecrow.analysisengine.service;
    exports org.rostilos.codecrow.analysisengine.service.rag;
    exports org.rostilos.codecrow.analysisengine.service.vcs;
    exports org.rostilos.codecrow.analysisengine.util;

    opens org.rostilos.codecrow.analysisengine.aiclient to spring.core, spring.beans, spring.context;
    opens org.rostilos.codecrow.analysisengine.config to spring.core, spring.beans, spring.context;
    opens org.rostilos.codecrow.analysisengine.processor to spring.core, spring.beans, spring.context;
    opens org.rostilos.codecrow.analysisengine.processor.analysis to spring.core, spring.beans, spring.context;
    opens org.rostilos.codecrow.analysisengine.service to spring.core, spring.beans, spring.context;
    opens org.rostilos.codecrow.analysisengine.service.rag to spring.core, spring.beans, spring.context;
    opens org.rostilos.codecrow.analysisengine.service.vcs to spring.core, spring.beans, spring.context;
}
