package org.rostilos.codecrow.plugins.javascript;

import org.rostilos.codecrow.plugins.ClasspathSyntaxPlugin;
import org.treesitter.TreeSitterJavascript;

import java.util.List;

public final class JavascriptPlugin extends ClasspathSyntaxPlugin {
    public JavascriptPlugin() {
        super(JavascriptPlugin.class, "javascript", List.of(".cjs", ".js", ".jsx", ".mjs"),
                "META-INF/codecrow/plugins/javascript/scopes.scm", TreeSitterJavascript::new);
    }
}
