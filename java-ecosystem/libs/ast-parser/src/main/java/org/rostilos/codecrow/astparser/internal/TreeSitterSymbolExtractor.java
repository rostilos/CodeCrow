package org.rostilos.codecrow.astparser.internal;

import org.rostilos.codecrow.astparser.api.ScopeResolver;
import org.rostilos.codecrow.astparser.api.SymbolExtractor;
import org.rostilos.codecrow.astparser.model.*;
import org.slf4j.Logger;
import org.slf4j.LoggerFactory;
import org.treesitter.TSNode;

import java.nio.charset.StandardCharsets;
import java.util.ArrayList;
import java.util.List;
import java.util.Set;

/**
 * Extracts symbol information from a parsed AST by walking the tree.
 * <p>
 * Replaces the Python RAG {@code /parse} endpoint for Java-side enrichment.
 * Extracts imports, classes, functions, calls, namespace, and parentClass
 * from the AST nodes — no regex, no LLM, pure structural extraction.
 *
 * <h3>Thread safety</h3>
 * Instances are thread-safe (no mutable state). ParsedTree is read-only.
 */
public final class TreeSitterSymbolExtractor implements SymbolExtractor {

    private static final Logger log = LoggerFactory.getLogger(TreeSitterSymbolExtractor.class);

    /** Node types that represent import/require statements across languages. */
    private static final Set<String> IMPORT_NODE_TYPES = Set.of(
            "import_declaration",           // Java, Go
            "import_statement",             // Python, JavaScript, TypeScript
            "import_from_statement",        // Python: from x import y
            "use_declaration",              // Rust
            "include_expression",           // PHP
            "require_call",                 // Ruby
            "using_directive",              // C#
            "namespace_use_declaration",    // PHP
            "preproc_include",             // C/C++
            "module_import"                // Haskell
    );

    /** Node types that represent function/method call expressions. */
    private static final Set<String> CALL_NODE_TYPES = Set.of(
            "call_expression",
            "method_invocation",            // Java
            "function_call_expression"      // PHP
    );

    private final ScopeResolver scopeResolver;

    public TreeSitterSymbolExtractor(ScopeResolver scopeResolver) {
        this.scopeResolver = scopeResolver;
    }

    @Override
    public SymbolInfo extract(ParsedTree parsedTree) {
        try {
            List<String> imports = new ArrayList<>();
            List<String> classes = new ArrayList<>();
            List<String> functions = new ArrayList<>();
            List<String> calls = new ArrayList<>();
            String[] namespace = {""};
            String[] parentClass = {""};

            walkForSymbols(parsedTree.getRootNode(), parsedTree.getSourceText(),
                    imports, classes, functions, calls, namespace, parentClass);

            List<ScopeInfo> scopes = scopeResolver.resolveAll(parsedTree);

            return new SymbolInfo(
                    List.copyOf(imports),
                    List.copyOf(classes),
                    List.copyOf(functions),
                    List.copyOf(calls),
                    namespace[0],
                    parentClass[0],
                    scopes
            );
        } catch (Exception e) {
            log.error("Symbol extraction failed for {}: {}",
                    parsedTree.getLanguage(), e.getMessage());
            return SymbolInfo.empty();
        }
    }

    private void walkForSymbols(TSNode node, String source,
                                List<String> imports, List<String> classes,
                                List<String> functions, List<String> calls,
                                String[] namespace, String[] parentClass) {
        String type = node.getType();

        // ── Imports ──────────────────────────────────────────────────
        if (IMPORT_NODE_TYPES.contains(type)) {
            String text = safeNodeText(node, source).trim();
            if (!text.isEmpty()) {
                imports.add(text);
            }
        }

        // ── Class/struct/interface definitions ───────────────────────
        if (isClassLike(type)) {
            String name = extractChildIdentifier(node, source);
            if (!name.isEmpty()) {
                classes.add(name);
            }
            // Extract parent class (superclass / extends)
            String parent = extractSuperclass(node, source);
            if (!parent.isEmpty() && parentClass[0].isEmpty()) {
                parentClass[0] = parent;
            }
        }

        // ── Function/method definitions ──────────────────────────────
        if (isFunctionLike(type)) {
            String name = extractChildIdentifier(node, source);
            if (!name.isEmpty()) {
                functions.add(name);
            }
        }

        // ── Function/method calls ────────────────────────────────────
        if (CALL_NODE_TYPES.contains(type)) {
            String callName = extractCallName(node, source);
            if (!callName.isEmpty()) {
                calls.add(callName);
            }
        }

        // ── Namespace / package ──────────────────────────────────────
        if (isNamespaceLike(type)) {
            String ns = extractNamespaceValue(node, source);
            if (!ns.isEmpty() && namespace[0].isEmpty()) {
                namespace[0] = ns;
            }
        }

        // Recurse into children
        int childCount = node.getNamedChildCount();
        for (int i = 0; i < childCount; i++) {
            walkForSymbols(node.getNamedChild(i), source,
                    imports, classes, functions, calls, namespace, parentClass);
        }
    }

    // ── Classification helpers ───────────────────────────────────────────

    private static boolean isClassLike(String type) {
        return switch (type) {
            case "class_definition", "class_declaration",
                 "struct_item", "impl_item",
                 "interface_declaration", "enum_declaration",
                 "trait_item", "object_declaration" -> true;
            default -> false;
        };
    }

    private static boolean isFunctionLike(String type) {
        return switch (type) {
            case "function_definition", "method_definition",
                 "function_declaration", "method_declaration",
                 "function_item", "arrow_function" -> true;
            default -> false;
        };
    }

    private static boolean isNamespaceLike(String type) {
        return switch (type) {
            case "package_declaration", "namespace_definition",
                 "namespace_declaration", "module_declaration" -> true;
            default -> false;
        };
    }

    // ── Extraction helpers ───────────────────────────────────────────────

    private static String extractChildIdentifier(TSNode node, String source) {
        for (int i = 0; i < node.getNamedChildCount(); i++) {
            TSNode child = node.getNamedChild(i);
            String childType = child.getType();
            if ("identifier".equals(childType) || "name".equals(childType)
                    || "type_identifier".equals(childType)
                    || "property_identifier".equals(childType)) {
                return safeNodeText(child, source).trim();
            }
        }
        return "";
    }

    private static String extractSuperclass(TSNode node, String source) {
        for (int i = 0; i < node.getNamedChildCount(); i++) {
            TSNode child = node.getNamedChild(i);
            String childType = child.getType();
            if ("superclass".equals(childType) || "superclasses".equals(childType)
                    || "extends_type".equals(childType) || "type_list".equals(childType)
                    || "argument_list".equals(childType)) {
                // The superclass node itself may contain an identifier
                return extractChildIdentifier(child, source);
            }
        }
        return "";
    }

    private static String extractCallName(TSNode node, String source) {
        // Call nodes typically have the function reference as first named child
        if (node.getNamedChildCount() > 0) {
            TSNode funcRef = node.getNamedChild(0);
            String type = funcRef.getType();
            if ("identifier".equals(type) || "member_expression".equals(type)
                    || "field_expression".equals(type) || "scoped_identifier".equals(type)
                    || "attribute".equals(type)) {
                return safeNodeText(funcRef, source).trim();
            }
        }
        return "";
    }

    private static String extractNamespaceValue(TSNode node, String source) {
        // Package/namespace nodes usually have the name as a child
        for (int i = 0; i < node.getNamedChildCount(); i++) {
            TSNode child = node.getNamedChild(i);
            String childType = child.getType();
            if ("scoped_identifier".equals(childType) || "identifier".equals(childType)
                    || "name".equals(childType) || "dotted_name".equals(childType)) {
                return safeNodeText(child, source).trim();
            }
        }
        return "";
    }

    /**
     * Safely extract text for a node using UTF-8 byte offsets.
     */
    private static String safeNodeText(TSNode node, String source) {
        // For single-line nodes, use row/column for correctness
        if (node.getStartPoint().getRow() == node.getEndPoint().getRow()) {
            String[] lines = source.split("\\r?\\n", -1);
            int row = node.getStartPoint().getRow();
            if (row < lines.length) {
                String line = lines[row];
                int startCol = Math.min(node.getStartPoint().getColumn(), line.length());
                int endCol = Math.min(node.getEndPoint().getColumn(), line.length());
                if (startCol <= endCol) {
                    return line.substring(startCol, endCol);
                }
            }
        }
        // Multi-line: use byte offsets
        byte[] bytes = source.getBytes(StandardCharsets.UTF_8);
        int start = node.getStartByte();
        int end = node.getEndByte();
        if (start >= 0 && end <= bytes.length && start < end) {
            return new String(bytes, start, end - start, StandardCharsets.UTF_8);
        }
        return "";
    }
}
