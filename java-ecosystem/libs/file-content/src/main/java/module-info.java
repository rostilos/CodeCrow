module org.rostilos.codecrow.filecontent {
    requires jakarta.persistence;
    requires spring.data.jpa;
    requires spring.data.commons;
    requires spring.context;
    requires spring.beans;
    requires spring.tx;
    requires spring.core;
    requires org.slf4j;

    requires org.rostilos.codecrow.core;

    // Model exports
    exports org.rostilos.codecrow.filecontent.model;
    exports org.rostilos.codecrow.filecontent.persistence;
    exports org.rostilos.codecrow.filecontent.service;

    // Open entities for Hibernate reflection
    opens org.rostilos.codecrow.filecontent.model
            to org.hibernate.orm.core, spring.beans, spring.context, spring.core;

    // Open repositories and services for Spring DI
    opens org.rostilos.codecrow.filecontent.persistence
            to spring.core, spring.beans, spring.context;
    opens org.rostilos.codecrow.filecontent.service
            to spring.core, spring.beans, spring.context;
}
