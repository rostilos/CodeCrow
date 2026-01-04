package org.rostilos.codecrow.mcp.generic;

public record FileDiffInfo(
        String filePath,
        String diffType,
        String rawContent,
        String diffContent
) {}
