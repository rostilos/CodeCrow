package org.rostilos.codecrow.analysisengine.dto.request.ai.enrichment;

/**
 * DTO representing a relationship between two files in the PR.
 * Used for building the dependency graph for intelligent batching.
 */
public record FileRelationshipDto(
        String sourceFile,
        String targetFile,
        RelationshipType relationshipType,
        String matchedOn
) {
    /**
     * Types of relationships between files.
     */
    public enum RelationshipType {
        IMPORTS,
        EXTENDS,
        IMPLEMENTS,
        CALLS
    }

    /**
     * Create an import relationship.
     */
    public static FileRelationshipDto imports(String sourceFile, String targetFile, String importStatement) {
        return new FileRelationshipDto(
                sourceFile,
                targetFile,
                RelationshipType.IMPORTS,
                importStatement
        );
    }

    /**
     * Create an extends relationship.
     */
    public static FileRelationshipDto extendsClass(String sourceFile, String targetFile, String className) {
        return new FileRelationshipDto(
                sourceFile,
                targetFile,
                RelationshipType.EXTENDS,
                className
        );
    }

    /**
     * Create an implements relationship.
     */
    public static FileRelationshipDto implementsInterface(String sourceFile, String targetFile, String interfaceName) {
        return new FileRelationshipDto(
                sourceFile,
                targetFile,
                RelationshipType.IMPLEMENTS,
                interfaceName
        );
    }

    /**
     * Create a calls relationship.
     */
    public static FileRelationshipDto calls(String sourceFile, String targetFile, String methodName) {
        return new FileRelationshipDto(
                sourceFile,
                targetFile,
                RelationshipType.CALLS,
                methodName
        );
    }
}
