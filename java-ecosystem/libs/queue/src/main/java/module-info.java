module org.rostilos.codecrow.queue {
    requires spring.context;
    requires spring.beans;
    requires spring.data.redis;
    requires spring.boot.autoconfigure;
    requires lettuce.core;

    exports org.rostilos.codecrow.queue;

    opens org.rostilos.codecrow.queue to spring.core, spring.beans, spring.context;
}
