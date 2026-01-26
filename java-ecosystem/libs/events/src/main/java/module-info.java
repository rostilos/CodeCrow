module codecrow.events {
    requires spring.context;
    requires org.rostilos.codecrow.core;

    exports org.rostilos.codecrow.events;
    exports org.rostilos.codecrow.events.analysis;
    exports org.rostilos.codecrow.events.rag;
    exports org.rostilos.codecrow.events.project;
    exports org.rostilos.codecrow.events.notification;
}
