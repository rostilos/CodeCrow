package org.rostilos.codecrow.analysisengine.dto.request.ai.enrichment;

/**
 * DTO representing a relationship between two files in the PR.
 * Used for building the dependency graph for intelligent batching.
 */
public record FileRelationshipDto(
        String sourceFile,
        String targetFile,
        RelationshipType relationshipType,
        String matchedOn,
        int strength
) {
    /**
     * Types of relationships between files.
     */
    public enum RelationshipType {
        IMPORTS,
        EXTENDS,
        IMPLEMENTS,
        CALLS,
        SAME_PACKAGE,
        REFERENCES
    }

    /**
     * Create an import relationship.
     */
    public static FileRelationshipDto imports(String sourceFile, String targetFile, String importStatement) {
        return new FileRelationshipDto(
                sourceFile,
                targetFile,
                RelationshipType.IMPORTS,
                importStatement,
                10 // High strength for direct imports
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
                className,
                15 // Highest strength for inheritance
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
                interfaceName,
                15 // Highest strength for interface implementation
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
                methodName,
                8 // Medium-high strength for method calls
        );
    }

    /**
     * Create a same-package relationship.
     */
    public static FileRelationshipDto samePackage(String sourceFile, String targetFile, String packageName) {
        return new FileRelationshipDto(
                sourceFile,
                targetFile,
                RelationshipType.SAME_PACKAGE,
                packageName,
                3 // Low strength for implicit package relationship
        );
    }
}
