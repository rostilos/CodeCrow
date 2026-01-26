module org.rostilos.codecrow.notification {
    requires spring.context;
    requires spring.boot;
    requires spring.boot.autoconfigure;
    requires spring.tx;
    requires spring.beans;
    requires spring.core;
    requires spring.data.jpa;
    requires spring.data.commons;
    requires jakarta.persistence;
    requires jakarta.validation;
    requires com.fasterxml.jackson.databind;
    requires org.slf4j;
    requires org.rostilos.codecrow.core;
    requires org.rostilos.codecrow.email;
    requires codecrow.events;

    exports org.rostilos.codecrow.notification.model;
    exports org.rostilos.codecrow.notification.service;
    exports org.rostilos.codecrow.notification.dto;
    exports org.rostilos.codecrow.notification.repository;
    exports org.rostilos.codecrow.notification.config;
    exports org.rostilos.codecrow.notification.listener;
    exports org.rostilos.codecrow.notification.scheduler;

    opens org.rostilos.codecrow.notification.model to spring.core, spring.beans, spring.context, org.hibernate.orm.core, com.fasterxml.jackson.databind;
    opens org.rostilos.codecrow.notification.service to spring.core, spring.beans, spring.context;
    opens org.rostilos.codecrow.notification.repository to spring.core, spring.beans, spring.context, spring.data.commons, spring.data.jpa;
    opens org.rostilos.codecrow.notification.config to spring.core, spring.beans, spring.context;
    opens org.rostilos.codecrow.notification.listener to spring.core, spring.beans, spring.context;
    opens org.rostilos.codecrow.notification.scheduler to spring.core, spring.beans, spring.context;
    opens org.rostilos.codecrow.notification.dto to spring.core, spring.beans, spring.context, com.fasterxml.jackson.databind;
}
