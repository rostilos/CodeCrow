package org.rostilos.codecrow.plugins;

/**
 * Optional contribution implemented by plugins that declare the neutral
 * {@code file-policy} capability.
 */
public interface FilePolicyPlugin extends CodeCrowPlugin {
    PluginOutcome<FileDisposition> fileDisposition(String normalizedPath);
}
