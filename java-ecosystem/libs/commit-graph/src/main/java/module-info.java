module org.rostilos.codecrow.commitgraph {
    requires jakarta.persistence;
    requires spring.data.jpa;
    requires spring.data.commons;
    requires spring.context;
    requires spring.beans;
    requires spring.tx;
    requires spring.core;
    requires org.slf4j;

    requires org.rostilos.codecrow.core;
    requires org.rostilos.codecrow.vcs;

    // Model exports
    exports org.rostilos.codecrow.commitgraph.model;
    exports org.rostilos.codecrow.commitgraph.persistence;
    exports org.rostilos.codecrow.commitgraph.service;
    exports org.rostilos.codecrow.commitgraph.dag;

    // Open entities for Hibernate reflection
    opens org.rostilos.codecrow.commitgraph.model
            to org.hibernate.orm.core, spring.beans, spring.context, spring.core;

    // Open services for Spring DI
    opens org.rostilos.codecrow.commitgraph.service
            to spring.core, spring.beans, spring.context;
    opens org.rostilos.codecrow.commitgraph.persistence
            to spring.core, spring.beans, spring.context;
    opens org.rostilos.codecrow.commitgraph.dag
            to spring.core, spring.beans, spring.context;
}
