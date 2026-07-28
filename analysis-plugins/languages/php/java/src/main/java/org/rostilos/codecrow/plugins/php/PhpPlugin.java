package org.rostilos.codecrow.plugins.php;

import org.rostilos.codecrow.plugins.ClasspathSyntaxPlugin;
import org.treesitter.TreeSitterPhp;

import java.util.List;

public final class PhpPlugin extends ClasspathSyntaxPlugin {
    public PhpPlugin() {
        super(PhpPlugin.class, "php", List.of(".inc", ".php", ".phtml"),
                "META-INF/codecrow/plugins/php/scopes.scm", TreeSitterPhp::new);
    }
}
