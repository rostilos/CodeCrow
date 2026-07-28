package org.rostilos.codecrow.plugins.cpp;

import org.rostilos.codecrow.plugins.ClasspathSyntaxPlugin;
import org.treesitter.TreeSitterCpp;

import java.util.List;

public final class CppPlugin extends ClasspathSyntaxPlugin {
    public CppPlugin() {
        super(CppPlugin.class, "cpp", List.of(".cc", ".cpp", ".cxx", ".h", ".hh", ".hpp", ".hxx"),
                "META-INF/codecrow/plugins/cpp/scopes.scm", TreeSitterCpp::new);
    }
}
