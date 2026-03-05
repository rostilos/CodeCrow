package org.rostilos.codecrow.astparser.internal;

import org.rostilos.codecrow.astparser.api.ScopeResolver;
import org.rostilos.codecrow.astparser.model.*;
import org.slf4j.Logger;
import org.slf4j.LoggerFactory;
import org.treesitter.*;

import java.util.*;

/**
 * Tree-sitter backed implementation of {@link ScopeResolver}.
 * <p>
 * Uses S-expression queries from {@link ScopeQueryRegistry} to declaratively
 * match scope-defining nodes in the AST. Each language has its own query file
 * ({@code queries/<lang>/scopes.scm}) that captures:
 * <ul>
 *   <li>{@code @function.def} — function/method/lambda definitions</li>
 *   <li>{@code @class.def} — class/struct/trait/interface/enum definitions</li>
 *   <li>{@code @block.def} — control-flow blocks (if/for/while/try)</li>
 *   <li>{@code @namespace.def} — package/namespace/module declarations</li>
 *   <li>{@code @name} — the name node of the scope (for extracting identifiers)</li>
 * </ul>
 *
 * <h3>Fallback</h3>
 * If no query file exists for a language, falls back to a generic tree-walking
 * approach that identifies scopes by universal node type patterns.
 *
 * <h3>Thread safety</h3>
 * Instances are thread-safe. TSQuery objects are cached per language.
 */
public final class TreeSitterScopeResolver implements ScopeResolver {

    private static final Logger log = LoggerFactory.getLogger(TreeSitterScopeResolver.class);

    private final ScopeQueryRegistry queryRegistry;
    private final ParserPool parserPool;

    /** Cache of compiled TSQuery objects per language. */
    private final Map<SupportedLanguage, TSQuery> compiledQueries =
            Collections.synchronizedMap(new EnumMap<>(SupportedLanguage.class));

    public TreeSitterScopeResolver(ScopeQueryRegistry queryRegistry, ParserPool parserPool) {
        this.queryRegistry = queryRegistry;
        this.parserPool = parserPool;
    }

    @Override
    public List<ScopeInfo> resolveAll(ParsedTree parsedTree) {
        Optional<TSQuery> queryOpt = getOrCompileQuery(parsedTree.getLanguage());
        List<ScopeInfo> scopes;
        if (queryOpt.isPresent()) {
            scopes = resolveWithQuery(parsedTree, queryOpt.get());
        } else {
            scopes = resolveWithTreeWalk(parsedTree);
        }

        // Sort by start line, then by end line descending (broader scopes first for same start)
        scopes.sort(Comparator.comparingInt(ScopeInfo::startLine)
                .thenComparing(Comparator.comparingInt(ScopeInfo::endLine).reversed()));

        // Assign parent indices
        return assignParents(scopes);
    }

    @Override
    public Optional<ScopeInfo> innermostScopeAt(ParsedTree parsedTree, int line) {
        List<ScopeInfo> chain = scopeChainAt(parsedTree, line);
        return chain.isEmpty() ? Optional.empty() : Optional.of(chain.get(0));
    }

    @Override
    public List<ScopeInfo> scopeChainAt(ParsedTree parsedTree, int line) {
        List<ScopeInfo> all = resolveAll(parsedTree);
        List<ScopeInfo> containing = new ArrayList<>();
        for (ScopeInfo scope : all) {
            if (scope.containsLine(line) && scope.kind() != ScopeKind.MODULE) {
                containing.add(scope);
            }
        }
        // Sort innermost first: narrower span = more specific
        containing.sort(Comparator.comparingInt(ScopeInfo::lineSpan));
        return containing;
    }

    // ── Query-based resolution ───────────────────────────────────────────

    private List<ScopeInfo> resolveWithQuery(ParsedTree parsedTree, TSQuery query) {
        List<ScopeInfo> scopes = new ArrayList<>();
        String[] sourceLines = parsedTree.getSourceText().split("\\r?\\n", -1);

        try (TSQueryCursor cursor = new TSQueryCursor()) {
            cursor.exec(query, parsedTree.getRootNode());
            TSQueryMatch match = new TSQueryMatch();

            while (cursor.nextMatch(match)) {
                TSQueryCapture[] captures = match.getCaptures();
                if (captures == null || captures.length == 0) continue;

                ScopeKind kind = null;
                TSNode scopeNode = null;
                String name = "";

                for (TSQueryCapture capture : captures) {
                    String captureName = query.getCaptureNameForId(capture.getIndex());
                    TSNode node = capture.getNode();

                    switch (captureName) {
                        case "function.def" -> {
                            kind = ScopeKind.FUNCTION;
                            scopeNode = node;
                        }
                        case "class.def" -> {
                            kind = ScopeKind.CLASS;
                            scopeNode = node;
                        }
                        case "block.def" -> {
                            kind = ScopeKind.BLOCK;
                            scopeNode = node;
                        }
                        case "namespace.def" -> {
                            kind = ScopeKind.NAMESPACE;
                            scopeNode = node;
                        }
                        case "name" -> name = extractNodeText(node, sourceLines, parsedTree.getSourceText());
                    }
                }

                if (kind != null && scopeNode != null) {
                    int startLine = scopeNode.getStartPoint().getRow() + 1; // tree-sitter is 0-based
                    int endLine = scopeNode.getEndPoint().getRow() + 1;
                    scopes.add(new ScopeInfo(kind, name, startLine, endLine));
                }
            }
        }

        return scopes;
    }

    // ── Generic tree-walk fallback ───────────────────────────────────────

    /**
     * Fallback for languages without .scm query files.
     * Walks the tree and identifies scopes by common tree-sitter node types.
     */
    private List<ScopeInfo> resolveWithTreeWalk(ParsedTree parsedTree) {
        List<ScopeInfo> scopes = new ArrayList<>();
        String[] sourceLines = parsedTree.getSourceText().split("\\r?\\n", -1);
        walkNode(parsedTree.getRootNode(), sourceLines, parsedTree.getSourceText(), scopes);
        return scopes;
    }

    private void walkNode(TSNode node, String[] sourceLines, String source, List<ScopeInfo> scopes) {
        String type = node.getType();
        ScopeKind kind = classifyNodeType(type);

        if (kind != null) {
            int startLine = node.getStartPoint().getRow() + 1;
            int endLine = node.getEndPoint().getRow() + 1;
            String name = extractNameFromNode(node, sourceLines, source);
            scopes.add(new ScopeInfo(kind, name, startLine, endLine));
        }

        int childCount = node.getNamedChildCount();
        for (int i = 0; i < childCount; i++) {
            TSNode child = node.getNamedChild(i);
            walkNode(child, sourceLines, source, scopes);
        }
    }

    /**
     * Classify a tree-sitter node type into a ScopeKind.
     * These node type strings are common across most tree-sitter grammars.
     */
    private static ScopeKind classifyNodeType(String nodeType) {
        if (nodeType == null) return null;
        return switch (nodeType) {
            // Functions / methods
            case "function_definition",
                 "method_definition",
                 "function_declaration",
                 "method_declaration",
                 "arrow_function",
                 "lambda_expression",
                 "function_item",         // Rust
                 "func_literal",          // Go
                 "function_literal" ->
                    ScopeKind.FUNCTION;

            // Classes / structs / traits / interfaces
            case "class_definition",
                 "class_declaration",
                 "struct_item",           // Rust
                 "impl_item",            // Rust
                 "interface_declaration",
                 "enum_declaration",
                 "trait_item",           // Rust
                 "type_declaration",     // Go
                 "object_declaration" -> // Scala
                    ScopeKind.CLASS;

            // Control-flow blocks
            case "if_statement",
                 "if_expression",
                 "for_statement",
                 "for_expression",
                 "while_statement",
                 "while_expression",
                 "do_statement",
                 "try_statement",
                 "catch_clause",
                 "switch_expression",
                 "switch_statement",
                 "match_expression",     // Rust/Scala
                 "case_clause",
                 "with_statement" ->
                    ScopeKind.BLOCK;

            // Namespace / package / module
            case "namespace_definition",
                 "namespace_declaration",
                 "module_declaration",
                 "package_declaration" ->
                    ScopeKind.NAMESPACE;

            default -> null;
        };
    }

    /**
     * Attempt to extract a name from a scope node by looking for common child node types.
     */
    private static String extractNameFromNode(TSNode node, String[] sourceLines, String source) {
        // Try named children with typical "name" or "identifier" fields
        for (int i = 0; i < node.getNamedChildCount(); i++) {
            TSNode child = node.getNamedChild(i);
            String type = child.getType();
            if ("identifier".equals(type) || "name".equals(type)
                    || "type_identifier".equals(type) || "property_identifier".equals(type)) {
                return extractNodeText(child, sourceLines, source);
            }
        }
        return "";
    }

    /**
     * Extract the text of a node from the source.
     * tree-sitter provides byte offsets, but Java strings are UTF-16.
     * We use row/column to safely extract text for single-line nodes (names).
     *
     * @param node        the AST node to extract text for
     * @param sourceLines pre-split source lines (avoids re-splitting per call)
     * @param source      the original source text (for multi-line byte-offset fallback)
     */
    private static String extractNodeText(TSNode node, String[] sourceLines, String source) {
        int startByte = node.getStartByte();
        int endByte = node.getEndByte();
        // tree-sitter byte offsets are UTF-8 byte positions.
        // For short identifier names (ASCII), byte offset == char offset.
        // For safety, use the line/column approach for single-line nodes.
        if (node.getStartPoint().getRow() == node.getEndPoint().getRow()) {
            int row = node.getStartPoint().getRow();
            if (row < sourceLines.length) {
                String line = sourceLines[row];
                int startCol = node.getStartPoint().getColumn();
                int endCol = node.getEndPoint().getColumn();
                if (startCol <= line.length() && endCol <= line.length()) {
                    return line.substring(startCol, endCol);
                }
            }
        }
        // Multi-line node text: use byte offsets with UTF-8 conversion
        byte[] sourceBytes = source.getBytes(java.nio.charset.StandardCharsets.UTF_8);
        if (startByte >= 0 && endByte <= sourceBytes.length && startByte < endByte) {
            return new String(sourceBytes, startByte, endByte - startByte, java.nio.charset.StandardCharsets.UTF_8);
        }
        return "";
    }

    // ── Query compilation & caching ──────────────────────────────────────

    private Optional<TSQuery> getOrCompileQuery(SupportedLanguage language) {
        TSQuery cached = compiledQueries.get(language);
        if (cached != null) return Optional.of(cached);

        return queryRegistry.getQuery(language).map(queryStr -> {
            TSLanguage grammar = parserPool.getGrammar(language);
            try {
                TSQuery query = new TSQuery(grammar, queryStr);
                compiledQueries.put(language, query);
                log.debug("Compiled scope query for {} ({} patterns, {} captures)",
                        language, query.getPatternCount(), query.getCaptureCount());
                return query;
            } catch (Exception e) {
                log.error("Failed to compile scope query for {}: {}", language, e.getMessage());
                return null;
            }
        });
    }

    // ── Parent assignment ────────────────────────────────────────────────

    /**
     * Assign parent indices based on containment.
     * Assumes scopes are sorted by startLine asc, endLine desc.
     */
    private static List<ScopeInfo> assignParents(List<ScopeInfo> scopes) {
        if (scopes.size() <= 1) return scopes;

        List<ScopeInfo> result = new ArrayList<>(scopes.size());
        // Stack of (index, scope) for active parent candidates
        Deque<int[]> stack = new ArrayDeque<>(); // [index, endLine]

        for (int i = 0; i < scopes.size(); i++) {
            ScopeInfo scope = scopes.get(i);

            // Pop scopes from stack that don't contain current scope
            while (!stack.isEmpty() && stack.peek()[1] < scope.endLine()) {
                stack.pop();
            }

            int parentIdx = stack.isEmpty() ? -1 : stack.peek()[0];
            result.add(new ScopeInfo(scope.kind(), scope.name(),
                    scope.startLine(), scope.endLine(), parentIdx));

            stack.push(new int[]{i, scope.endLine()});
        }

        return result;
    }
}
