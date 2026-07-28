package org.rostilos.codecrow.plugins.scala;

import org.rostilos.codecrow.plugins.ClasspathSyntaxPlugin;
import org.treesitter.TreeSitterScala;

import java.util.List;

public final class ScalaPlugin extends ClasspathSyntaxPlugin {
    public ScalaPlugin() {
        super(ScalaPlugin.class, "scala", List.of(".sc", ".scala"),
                "META-INF/codecrow/plugins/scala/scopes.scm", TreeSitterScala::new);
    }
}
