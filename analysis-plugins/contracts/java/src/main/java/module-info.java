module org.rostilos.codecrow.plugins {
    requires transitive tree.sitter;
    requires com.fasterxml.jackson.databind;
    exports org.rostilos.codecrow.plugins;
    uses org.rostilos.codecrow.plugins.CodeCrowPlugin;
    opens org.rostilos.codecrow.plugins to com.fasterxml.jackson.databind;
}
