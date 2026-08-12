module org.rostilos.codecrow.publicshare {
    requires jakarta.persistence;
    requires spring.data.jpa;
    requires spring.data.commons;
    requires spring.context;
    requires spring.boot.autoconfigure;
    requires spring.beans;
    requires spring.core;
    requires spring.tx;
    requires org.hibernate.orm.core;

    exports org.rostilos.codecrow.publicshare.api;
    exports org.rostilos.codecrow.publicshare.config;
    exports org.rostilos.codecrow.publicshare.service;

    opens org.rostilos.codecrow.publicshare.config
            to spring.core, spring.beans, spring.context;
    opens org.rostilos.codecrow.publicshare.model
            to org.hibernate.orm.core, spring.core, spring.beans, spring.context;
    opens org.rostilos.codecrow.publicshare.persistence
            to spring.core, spring.beans, spring.context;
    opens org.rostilos.codecrow.publicshare.service
            to spring.core, spring.beans, spring.context;
}
