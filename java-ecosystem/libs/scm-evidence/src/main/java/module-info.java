module org.rostilos.codecrow.scmevidence {
    requires jakarta.persistence;
    requires spring.data.jpa;
    requires spring.data.commons;
    requires spring.context;
    requires spring.boot.autoconfigure;
    requires spring.tx;
    requires org.slf4j;
    requires org.rostilos.codecrow.vcs;

    exports org.rostilos.codecrow.scmevidence.api;
    exports org.rostilos.codecrow.scmevidence.config;
    exports org.rostilos.codecrow.scmevidence.model;
    exports org.rostilos.codecrow.scmevidence.persistence;
    exports org.rostilos.codecrow.scmevidence.service;

    opens org.rostilos.codecrow.scmevidence.model
            to org.hibernate.orm.core, spring.core, spring.context;
    opens org.rostilos.codecrow.scmevidence.persistence
            to spring.core, spring.context;
    opens org.rostilos.codecrow.scmevidence.service
            to spring.core, spring.context;
    opens org.rostilos.codecrow.scmevidence.config
            to spring.core, spring.context;
}
