module codecrow.task.management {
    requires org.rostilos.codecrow.core;
    requires okhttp3;
    requires com.fasterxml.jackson.databind;
    requires org.slf4j;
    requires spring.context;
    requires spring.beans;

    exports org.rostilos.codecrow.taskmanagement;
    exports org.rostilos.codecrow.taskmanagement.model;
    exports org.rostilos.codecrow.taskmanagement.jira.cloud;

    opens org.rostilos.codecrow.taskmanagement to spring.core, spring.beans, spring.context;
    opens org.rostilos.codecrow.taskmanagement.jira.cloud to spring.core, spring.beans, spring.context;
}
