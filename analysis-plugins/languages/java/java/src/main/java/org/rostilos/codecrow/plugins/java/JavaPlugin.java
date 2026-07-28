package org.rostilos.codecrow.plugins.java;

import org.rostilos.codecrow.plugins.ClasspathSyntaxPlugin;
import org.treesitter.TreeSitterJava;

import java.util.List;

public final class JavaPlugin extends ClasspathSyntaxPlugin {
    public JavaPlugin() {
        super(JavaPlugin.class, "java", List.of(".java"),
                "META-INF/codecrow/plugins/java/scopes.scm", TreeSitterJava::new);
    }
}
