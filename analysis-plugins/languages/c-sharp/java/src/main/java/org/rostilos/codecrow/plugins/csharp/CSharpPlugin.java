package org.rostilos.codecrow.plugins.csharp;

import org.rostilos.codecrow.plugins.ClasspathSyntaxPlugin;
import org.treesitter.TreeSitterCSharp;

import java.util.List;

public final class CSharpPlugin extends ClasspathSyntaxPlugin {
    public CSharpPlugin() {
        super(CSharpPlugin.class, "c-sharp", List.of(".cs"),
                "META-INF/codecrow/plugins/c-sharp/scopes.scm", TreeSitterCSharp::new);
    }
}
