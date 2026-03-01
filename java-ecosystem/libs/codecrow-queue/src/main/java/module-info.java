module org.rostilos.codecrow.queue {
    requires spring.context;
    requires spring.beans;
    requires spring.core;
    requires spring.data.redis;
    requires org.slf4j;
    requires com.fasterxml.jackson.databind;
    requires org.rostilos.codecrow.core;

    exports org.rostilos.codecrow.queue;
    exports org.rostilos.codecrow.queue.dto;
}
