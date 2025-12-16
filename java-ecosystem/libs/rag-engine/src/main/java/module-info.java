module org.rostilos.codecrow.ragengine {
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
    requires org.rostilos.codecrow.analysisengine;
    requires okhttp3;
    requires org.slf4j;
    requires com.fasterxml.jackson.databind;
    requires kotlin.stdlib;

    exports org.rostilos.codecrow.ragengine.client;
    exports org.rostilos.codecrow.ragengine.service;

    opens org.rostilos.codecrow.ragengine.client to spring.core, spring.beans, spring.context;
    opens org.rostilos.codecrow.ragengine.service to spring.core, spring.beans, spring.context;
}
