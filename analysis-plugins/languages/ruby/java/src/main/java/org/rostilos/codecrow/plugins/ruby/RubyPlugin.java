package org.rostilos.codecrow.plugins.ruby;

import org.rostilos.codecrow.plugins.ClasspathSyntaxPlugin;
import org.treesitter.TreeSitterRuby;

import java.util.List;

public final class RubyPlugin extends ClasspathSyntaxPlugin {
    public RubyPlugin() {
        super(RubyPlugin.class, "ruby", List.of(".rb"),
                "META-INF/codecrow/plugins/ruby/scopes.scm", TreeSitterRuby::new);
    }
}
