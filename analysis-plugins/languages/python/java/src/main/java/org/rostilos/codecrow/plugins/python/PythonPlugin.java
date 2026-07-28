package org.rostilos.codecrow.plugins.python;

import org.rostilos.codecrow.plugins.ClasspathSyntaxPlugin;
import org.treesitter.TreeSitterPython;

import java.util.List;

public final class PythonPlugin extends ClasspathSyntaxPlugin {
    public PythonPlugin() {
        super(PythonPlugin.class, "python", List.of(".py", ".pyi", ".pyw"),
                "META-INF/codecrow/plugins/python/scopes.scm", TreeSitterPython::new);
    }
}
