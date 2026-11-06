package org.rostilos.codecrow.plugins.magento;

import org.rostilos.codecrow.plugins.CodeCrowPlugin;
import org.rostilos.codecrow.plugins.FileDisposition;
import org.rostilos.codecrow.plugins.FilePolicyPlugin;
import org.rostilos.codecrow.plugins.PluginDescriptor;
import org.rostilos.codecrow.plugins.PluginManifestLoader;
import org.rostilos.codecrow.plugins.PluginOutcome;

import java.util.Arrays;
import java.util.HashSet;
import java.util.Locale;
import java.util.Set;
import java.util.regex.Pattern;

public final class MagentoPlugin implements CodeCrowPlugin, FilePolicyPlugin {
    private static final Set<String> MAGENTO_CONFIG_AREAS = Set.of(
            "adminhtml", "crontab", "frontend", "graphql", "webapi_rest", "webapi_soap");
    private static final Set<String> MAGENTO_VIEW_AREAS = Set.of(
            "adminhtml", "base", "frontend");
    private static final Set<String> VENDOR_ARCHITECTURE_FILENAMES = Set.of(
            "composer.json", "registration.php", "theme.xml", "requirejs-config.js");
    private static final Set<String> VENDOR_FRONTEND_SUFFIXES = Set.of(
            "phtml", "js", "mjs", "ts", "tsx", "jsx", "css", "less", "html",
            "graphql", "gql");
    private static final Pattern MAGENTO_COMPONENT_NAME = Pattern.compile(
            "[a-z][a-z0-9]*_[a-z][a-z0-9]*");

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
        String normalized = path.replace('\\', '/').toLowerCase(Locale.ROOT);
        while (normalized.startsWith("/")) normalized = normalized.substring(1);
        while (normalized.endsWith("/")) {
            normalized = normalized.substring(0, normalized.length() - 1);
        }
        String folded = "/" + normalized;
        if (folded.startsWith("/generated/")
                || folded.startsWith("/var/")
                || folded.startsWith("/pub/static/")) {
            return PluginOutcome.handled(FileDisposition.GENERATED);
        }
        if (folded.startsWith("/dev/")) {
            return PluginOutcome.handled(FileDisposition.EXCLUDED);
        }
        Set<String> segments = new HashSet<>(Arrays.asList(folded.substring(1).split("/")));
        if (folded.startsWith("/vendor/")
                && (segments.contains("test") || segments.contains("tests"))) {
            return PluginOutcome.handled(FileDisposition.EXCLUDED);
        }
        if (folded.endsWith(".graphqls")
                || (folded.endsWith("/db_schema_whitelist.json") && folded.contains("/etc/"))
                || (folded.endsWith(".xml")
                    && (isMagentoConfigXml(normalized)
                        || folded.contains("/layout/")
                        || folded.contains("/page_layout/")
                        || isMagentoLayoutsManifest(normalized)
                        || folded.contains("/ui_component/")))) {
            return PluginOutcome.handled(FileDisposition.ARCHITECTURE_ONLY);
        }
        if (folded.startsWith("/vendor/")) {
            String filename = folded.substring(folded.lastIndexOf('/') + 1);
            if (VENDOR_ARCHITECTURE_FILENAMES.contains(filename)) {
                return PluginOutcome.handled(FileDisposition.ARCHITECTURE_ONLY);
            }
            int dot = folded.lastIndexOf('.');
            String suffix = dot >= 0 ? folded.substring(dot + 1) : "";
            if (Set.of("php", "inc").contains(suffix)) {
                return PluginOutcome.handled(FileDisposition.FULL);
            }
            if (VENDOR_FRONTEND_SUFFIXES.contains(suffix)
                    && (folded.contains("/view/") || folded.contains("/web/"))) {
                return PluginOutcome.handled(FileDisposition.ARCHITECTURE_ONLY);
            }
            return PluginOutcome.handled(FileDisposition.EXCLUDED);
        }
        return PluginOutcome.handled(FileDisposition.FULL);
    }

    /**
     * Magento merges XML directly below {@code etc} and below its known areas.
     * Custom or deeper descendants are ordinary payloads, not configuration.
     */
    private static boolean isMagentoConfigXml(String normalized) {
        if (!normalized.endsWith(".xml")) return false;
        String candidate = "/" + normalized;
        String marker = "/etc/";
        int markerIndex = candidate.lastIndexOf(marker);
        if (markerIndex < 0) return false;
        String[] tail = candidate.substring(markerIndex + marker.length()).split("/", -1);
        return tail.length == 1
                || (tail.length == 2 && MAGENTO_CONFIG_AREAS.contains(tail[0]));
    }

    /** Page-layout declarations live below a module view area or theme component root. */
    private static boolean isMagentoLayoutsManifest(String normalized) {
        String[] segments = normalized.split("/");
        if (segments.length < 2 || !"layouts.xml".equals(segments[segments.length - 1])) {
            return false;
        }
        if (segments.length >= 3) {
            int view = segments.length - 3;
            if ("view".equals(segments[view])
                    && MAGENTO_VIEW_AREAS.contains(segments[view + 1])) {
                return true;
            }
        }
        return MAGENTO_COMPONENT_NAME.matcher(segments[segments.length - 2]).matches();
    }
}
