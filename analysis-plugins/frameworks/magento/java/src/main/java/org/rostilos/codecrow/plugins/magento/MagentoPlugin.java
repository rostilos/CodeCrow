package org.rostilos.codecrow.plugins.magento;

import org.rostilos.codecrow.plugins.CodeCrowPlugin;
import org.rostilos.codecrow.plugins.FileDisposition;
import org.rostilos.codecrow.plugins.FilePolicyPlugin;
import org.rostilos.codecrow.plugins.PluginDescriptor;
import org.rostilos.codecrow.plugins.PluginManifestLoader;
import org.rostilos.codecrow.plugins.PluginOutcome;

import java.util.Locale;
import java.util.HashSet;
import java.util.Set;

public final class MagentoPlugin implements CodeCrowPlugin, FilePolicyPlugin {
    private final PluginDescriptor descriptor;

    public MagentoPlugin() {
        try (var input = MagentoPlugin.class.getResourceAsStream(
                "/META-INF/codecrow/plugins/magento/plugin.json")) {
            descriptor = new PluginManifestLoader().loadDescriptor(input);
        } catch (Exception exception) {
            throw new IllegalStateException("cannot load Magento plugin descriptor", exception);
        }
    }

    @Override
    public PluginDescriptor descriptor() {
        return descriptor;
    }

    @Override
    public PluginOutcome<FileDisposition> fileDisposition(String path) {
        String folded = "/" + path.toLowerCase(Locale.ROOT);
        if (folded.startsWith("/generated/")
                || folded.startsWith("/var/")
                || folded.startsWith("/pub/static/")) {
            return PluginOutcome.handled(FileDisposition.GENERATED);
        }
        if (folded.startsWith("/dev/")) {
            return PluginOutcome.handled(FileDisposition.EXCLUDED);
        }
        Set<String> segments = new HashSet<>(
                java.util.Arrays.asList(folded.substring(1).split("/")));
        if (folded.startsWith("/vendor/")
                && (segments.contains("test") || segments.contains("tests"))) {
            return PluginOutcome.handled(FileDisposition.EXCLUDED);
        }
        if (folded.endsWith(".graphqls")
                || (folded.endsWith("/db_schema_whitelist.json") && folded.contains("/etc/"))
                || (folded.endsWith(".xml")
                    && (folded.contains("/etc/")
                        || folded.contains("/layout/")
                        || folded.contains("/ui_component/")))) {
            return PluginOutcome.handled(FileDisposition.ARCHITECTURE_ONLY);
        }
        if (folded.startsWith("/vendor/")) {
            String filename = folded.substring(folded.lastIndexOf('/') + 1);
            if (Set.of(
                    "composer.json", "registration.php", "theme.xml", "requirejs-config.js")
                    .contains(filename)) {
                return PluginOutcome.handled(FileDisposition.ARCHITECTURE_ONLY);
            }
            int dot = folded.lastIndexOf('.');
            String suffix = dot >= 0 ? folded.substring(dot + 1) : "";
            if (Set.of("php", "inc").contains(suffix)) {
                return PluginOutcome.handled(FileDisposition.FULL);
            }
            if (Set.of("phtml", "js", "mjs", "ts", "css", "less", "html").contains(suffix)
                    && (folded.contains("/view/") || folded.contains("/web/"))) {
                return PluginOutcome.handled(FileDisposition.ARCHITECTURE_ONLY);
            }
            return PluginOutcome.handled(FileDisposition.EXCLUDED);
        }
        return PluginOutcome.handled(FileDisposition.FULL);
    }
}
