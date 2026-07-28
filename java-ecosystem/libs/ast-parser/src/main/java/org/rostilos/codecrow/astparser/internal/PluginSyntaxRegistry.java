package org.rostilos.codecrow.astparser.internal;

import org.rostilos.codecrow.astparser.model.SupportedLanguage;
import org.rostilos.codecrow.plugins.CodeCrowPlugin;
import org.rostilos.codecrow.plugins.OutcomeStatus;
import org.rostilos.codecrow.plugins.PluginCapability;
import org.rostilos.codecrow.plugins.SyntaxContribution;
import org.rostilos.codecrow.plugins.SyntaxPlugin;
import org.slf4j.Logger;
import org.slf4j.LoggerFactory;

import java.io.BufferedReader;
import java.io.IOException;
import java.io.InputStreamReader;
import java.nio.charset.StandardCharsets;
import java.util.ArrayList;
import java.util.Comparator;
import java.util.HashMap;
import java.util.List;
import java.util.Map;
import java.util.Optional;
import java.util.ServiceLoader;
import java.util.stream.Collectors;

/** Resolves syntax contributions without coupling the AST host to plugin implementations. */
public final class PluginSyntaxRegistry {
    private static final Logger log = LoggerFactory.getLogger(PluginSyntaxRegistry.class);

    private record LoadedSyntax(SyntaxPlugin plugin, SyntaxContribution contribution,
                                SupportedLanguage language) {}

    private final Map<String, LoadedSyntax> byExtension;
    private final Map<String, LoadedSyntax> byLanguage;

    private PluginSyntaxRegistry(List<SyntaxPlugin> plugins) {
        Map<String, LoadedSyntax> extensions = new HashMap<>();
        Map<String, LoadedSyntax> languages = new HashMap<>();
        plugins.stream()
                .sorted(Comparator.comparing(plugin -> plugin.descriptor().id()))
                .forEach(plugin -> register(plugin, extensions, languages));
        this.byExtension = Map.copyOf(extensions);
        this.byLanguage = Map.copyOf(languages);
    }

    public static PluginSyntaxRegistry discover() {
        List<SyntaxPlugin> plugins = new ArrayList<>();
        ServiceLoader.load(CodeCrowPlugin.class, Thread.currentThread().getContextClassLoader())
                .forEach(plugin -> {
            if (plugin instanceof SyntaxPlugin syntaxPlugin) plugins.add(syntaxPlugin);
        });
        return new PluginSyntaxRegistry(plugins);
    }

    static PluginSyntaxRegistry of(List<SyntaxPlugin> plugins) {
        return new PluginSyntaxRegistry(plugins);
    }

    public Optional<SupportedLanguage> languageFor(String filePath) {
        if (filePath == null || filePath.isBlank()) return Optional.empty();
        int dot = filePath.lastIndexOf('.');
        if (dot < 0 || dot == filePath.length() - 1) return Optional.empty();
        String extension = "." + filePath.substring(dot + 1).toLowerCase();
        return Optional.ofNullable(byExtension.get(extension)).map(LoadedSyntax::language);
    }

    public Optional<String> scopeQuery(SupportedLanguage language) {
        LoadedSyntax loaded = byLanguage.get(language.id());
        if (loaded == null) return Optional.empty();
        String resource = loaded.contribution().scopeQueryResource();
        if (resource.isBlank()) return Optional.empty();
        try (var input = loaded.plugin().getClass().getClassLoader().getResourceAsStream(resource)) {
            if (input == null) {
                log.warn("Syntax plugin {} has no classpath resource {}",
                        loaded.plugin().descriptor().id(), resource);
                return Optional.empty();
            }
            String query = new BufferedReader(new InputStreamReader(input, StandardCharsets.UTF_8))
                    .lines()
                    .collect(Collectors.joining("\n"));
            return query.isBlank() ? Optional.empty() : Optional.of(query);
        } catch (IOException exception) {
            log.warn("Cannot read syntax contribution from plugin {}: {}",
                    loaded.plugin().descriptor().id(), exception.getMessage());
            return Optional.empty();
        }
    }

    public List<SupportedLanguage> languages() {
        return byLanguage.values().stream()
                .map(LoadedSyntax::language)
                .sorted(Comparator.comparing(SupportedLanguage::id))
                .toList();
    }

    private static void register(
            SyntaxPlugin plugin,
            Map<String, LoadedSyntax> extensions,
            Map<String, LoadedSyntax> languages) {
        if (!plugin.descriptor().capabilities().contains(PluginCapability.SYNTAX)) {
            log.warn("Ignoring syntax implementation from plugin {} without syntax capability",
                    plugin.descriptor().id());
            return;
        }
        var outcome = plugin.syntaxContribution();
        if (outcome.status() != OutcomeStatus.HANDLED) {
            if (outcome.status() == OutcomeStatus.FAILED) {
                log.warn("Syntax plugin {} failed: {}", plugin.descriptor().id(),
                        outcome.diagnostic().message());
            }
            return;
        }
        SyntaxContribution contribution = outcome.value();
        SupportedLanguage language = new SupportedLanguage(contribution);
        LoadedSyntax loaded = new LoadedSyntax(plugin, contribution, language);
        for (String extension : contribution.extensions()) {
            LoadedSyntax previous = extensions.putIfAbsent(extension, loaded);
            if (previous != null) {
                throw new IllegalStateException("multiple syntax plugins claim extension " + extension);
            }
        }
        LoadedSyntax previous = languages.putIfAbsent(language.id(), loaded);
        if (previous != null) {
            throw new IllegalStateException("multiple syntax plugins claim language " + language.id());
        }
    }
}
