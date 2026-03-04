package org.rostilos.codecrow.analysisengine.service;

import org.rostilos.codecrow.astparser.AstParserFacade;
import org.rostilos.codecrow.astparser.model.ParsedTree;
import org.rostilos.codecrow.astparser.model.ScopeInfo;
import org.rostilos.codecrow.astparser.model.ScopeKind;
import org.rostilos.codecrow.core.model.codeanalysis.CodeAnalysisIssue;
import org.rostilos.codecrow.core.model.codeanalysis.IssueScope;
import org.rostilos.codecrow.core.util.tracking.LineHashSequence;
import org.slf4j.Logger;
import org.slf4j.LoggerFactory;
import org.springframework.stereotype.Service;

import jakarta.annotation.PreDestroy;
import java.util.List;
import java.util.Map;
import java.util.Optional;

/**
 * Enriches newly created {@link CodeAnalysisIssue}s with AST-resolved scope boundaries.
 * <p>
 * After the AI produces issues and snippet anchoring corrects line numbers, this service
 * parses each affected file with tree-sitter and resolves the innermost scope (function,
 * class, block) that contains each issue's anchor line. The resolved boundaries are written
 * to {@code scopeStartLine} and {@code endLineNumber}, and the context hash is recomputed
 * using the AST-derived scope instead of heuristic snippet lengths.
 * <p>
 * This service lives in {@code analysis-engine} (not in {@code core}) because
 * {@code ast-parser} depends on {@code core}, making a reverse dependency circular.
 *
 * <h3>Thread safety</h3>
 * Thread-safe. The underlying {@link AstParserFacade} uses a parser pool.
 */
@Service
public class AstScopeEnricher {

    private static final Logger log = LoggerFactory.getLogger(AstScopeEnricher.class);

    private final AstParserFacade astParserFacade;

    public AstScopeEnricher() {
        this.astParserFacade = AstParserFacade.createDefault();
    }

    /**
     * Enrich a list of issues with AST-resolved scope boundaries.
     * <p>
     * For each issue that has a valid file path and line number in a supported language,
     * resolves the innermost enclosing scope and sets:
     * <ul>
     *   <li>{@code scopeStartLine} — start of the enclosing scope</li>
     *   <li>{@code endLineNumber} — end of the enclosing scope</li>
     *   <li>{@code lineHashContext} — scope-aware content hash (replaces snippet-heuristic hash)</li>
     * </ul>
     *
     * @param issues       issues to enrich (modified in place)
     * @param fileContents map of filePath → full file content
     */
    public void enrichWithAstScopes(List<CodeAnalysisIssue> issues, Map<String, String> fileContents) {
        if (issues == null || issues.isEmpty() || fileContents == null || fileContents.isEmpty()) {
            return;
        }

        int enriched = 0;
        int skipped = 0;

        for (CodeAnalysisIssue issue : issues) {
            if (issue == null) continue;

            String filePath = issue.getFilePath();
            Integer lineNumber = issue.getLineNumber();

            // Skip issues without a valid anchor
            if (filePath == null || lineNumber == null || lineNumber <= 0) {
                skipped++;
                continue;
            }

            // Skip unsupported languages
            if (!astParserFacade.isSupported(filePath)) {
                skipped++;
                continue;
            }

            String fileContent = fileContents.get(filePath);
            if (fileContent == null || fileContent.isEmpty()) {
                skipped++;
                continue;
            }

            try {
                Optional<ScopeInfo> scopeOpt = astParserFacade.resolveInnermostScope(
                        filePath, fileContent, lineNumber);

                if (scopeOpt.isPresent()) {
                    ScopeInfo scope = scopeOpt.get();

                    // Set AST-resolved scope boundaries
                    issue.setScopeStartLine(scope.startLine());
                    issue.setEndLineNumber(scope.endLine());

                    // Upgrade issue scope based on AST if the AI only said LINE
                    // but the enclosing scope is clearly a function/class/block
                    upgradeIssueScopeFromAst(issue, scope);

                    // Recompute context hash using AST scope boundaries
                    String scopeHash = astParserFacade.computeContextHash(
                            fileContent, lineNumber, scope);
                    if (scopeHash != null && !scopeHash.isEmpty()) {
                        issue.setLineHashContext(scopeHash);
                    }

                    enriched++;
                    log.debug("AST scope resolved for {}:{} → {} '{}' [{}-{}]",
                            filePath, lineNumber, scope.kind(), scope.name(),
                            scope.startLine(), scope.endLine());
                } else {
                    // No scope found — line is at module level.
                    // Keep the issue as-is (snippet-heuristic context hash stays).
                    log.debug("No enclosing scope at {}:{} — module-level code", filePath, lineNumber);
                    skipped++;
                }
            } catch (Exception e) {
                log.warn("AST scope resolution failed for {}:{}: {}", filePath, lineNumber, e.getMessage());
                skipped++;
            }
        }

        log.info("AST scope enrichment: {} enriched, {} skipped out of {} issues",
                enriched, skipped, issues.size());
    }

    /**
     * Parse all files in the map and return parsed trees.
     * <p>
     * The caller must close the returned trees when done. Useful when the same
     * parsed trees need to be passed to multiple consumers (e.g. enricher + reconciliation engine).
     *
     * @param fileContents map of filePath → full file content
     * @return map of filePath → ParsedTree (only for supported/parseable files)
     */
    public Map<String, ParsedTree> parseAll(Map<String, String> fileContents) {
        Map<String, ParsedTree> trees = new java.util.HashMap<>();
        if (fileContents == null) return trees;

        for (Map.Entry<String, String> entry : fileContents.entrySet()) {
            String filePath = entry.getKey();
            String content = entry.getValue();

            if (content == null || content.isEmpty() || !astParserFacade.isSupported(filePath)) {
                continue;
            }

            try {
                ParsedTree tree = astParserFacade.parse(filePath, content);
                trees.put(filePath, tree);
            } catch (Exception e) {
                log.warn("Failed to parse {} for AST: {}", filePath, e.getMessage());
            }
        }

        log.debug("Parsed {} of {} files for AST scope resolution",
                trees.size(), fileContents.size());
        return trees;
    }

    /**
     * Enrich issues using pre-parsed trees (avoids double parsing).
     *
     * @param issues       issues to enrich (modified in place)
     * @param fileContents map of filePath → full file content (for hash computation)
     * @param parsedTrees  map of filePath → pre-parsed AST tree
     */
    public void enrichWithParsedTrees(List<CodeAnalysisIssue> issues,
                                      Map<String, String> fileContents,
                                      Map<String, ParsedTree> parsedTrees) {
        if (issues == null || issues.isEmpty() || parsedTrees == null || parsedTrees.isEmpty()) {
            return;
        }

        int enriched = 0;
        int skipped = 0;

        for (CodeAnalysisIssue issue : issues) {
            if (issue == null) continue;

            String filePath = issue.getFilePath();
            Integer lineNumber = issue.getLineNumber();

            if (filePath == null || lineNumber == null || lineNumber <= 0) {
                skipped++;
                continue;
            }

            ParsedTree tree = parsedTrees.get(filePath);
            if (tree == null) {
                skipped++;
                continue;
            }

            String fileContent = fileContents.get(filePath);
            if (fileContent == null || fileContent.isEmpty()) {
                skipped++;
                continue;
            }

            try {
                Optional<ScopeInfo> scopeOpt = astParserFacade.getScopeResolver()
                        .innermostScopeAt(tree, lineNumber);

                if (scopeOpt.isPresent()) {
                    ScopeInfo scope = scopeOpt.get();

                    issue.setScopeStartLine(scope.startLine());
                    issue.setEndLineNumber(scope.endLine());
                    upgradeIssueScopeFromAst(issue, scope);

                    String scopeHash = astParserFacade.computeContextHash(
                            fileContent, lineNumber, scope);
                    if (scopeHash != null && !scopeHash.isEmpty()) {
                        issue.setLineHashContext(scopeHash);
                    }

                    enriched++;
                } else {
                    skipped++;
                }
            } catch (Exception e) {
                log.warn("AST scope resolution failed for {}:{}: {}", filePath, lineNumber, e.getMessage());
                skipped++;
            }
        }

        log.info("AST scope enrichment (pre-parsed): {} enriched, {} skipped out of {} issues",
                enriched, skipped, issues.size());
    }

    /**
     * Parse a single file if the language is supported, otherwise return {@code null}.
     * <p>
     * Convenience method for callers that need a nullable tree to pass into
     * {@link IssueReconciliationEngine} AST overloads. The caller must close a non-null result.
     *
     * @param filePath file path (for language detection)
     * @param content  full file content (may be null)
     * @return parsed tree, or {@code null} if unsupported / unparseable / null content
     */
    public ParsedTree tryParse(String filePath, String content) {
        if (content == null || filePath == null || !astParserFacade.isSupported(filePath)) {
            return null;
        }
        try {
            return astParserFacade.parse(filePath, content);
        } catch (Exception e) {
            log.debug("AST parse failed for {} (non-critical): {}", filePath, e.getMessage());
            return null;
        }
    }

    public AstParserFacade getFacade() {
        return astParserFacade;
    }

    @PreDestroy
    public void close() {
        astParserFacade.close();
    }

    // ── Private helpers ──────────────────────────────────────────────────

    /**
     * Upgrade the issue's scope if the AST reveals a broader enclosing construct.
     * <p>
     * If the AI said "LINE" but the innermost scope is a function, upgrade to FUNCTION.
     * If AI already set FUNCTION or FILE, respect it — only upgrade, never downgrade.
     * <p>
     * Special handling for <em>unanchored</em> issues (line ≤ 1, no codeSnippet):
     * these have a meaningless LINE scope because there is no real single-line reference.
     * For such issues, always upgrade to the AST scope.
     */
    private static void upgradeIssueScopeFromAst(CodeAnalysisIssue issue, ScopeInfo scope) {
        IssueScope currentScope = issue.getIssueScope();
        if (currentScope == IssueScope.FILE) {
            return; // FILE scope is the broadest — never change
        }

        // Map AST ScopeKind to IssueScope
        IssueScope astScope = mapScopeKindToIssueScope(scope.kind());
        if (astScope == null) return;

        // Only upgrade: LINE → BLOCK → FUNCTION, never downgrade
        if (currentScope == null || scopeOrdinal(currentScope) < scopeOrdinal(astScope)) {
            boolean isUnanchored = (issue.getLineNumber() != null && issue.getLineNumber() <= 1)
                    && (issue.getCodeSnippet() == null || issue.getCodeSnippet().isBlank());
            boolean isAtScopeStart = issue.getLineNumber() != null
                    && issue.getLineNumber() == scope.startLine();

            if (isUnanchored) {
                // Unanchored issues have no meaningful line reference —
                // LINE scope is a default/placeholder. Always upgrade to AST scope.
                issue.setIssueScope(astScope);
                log.debug("Upgraded scope from {} to {} for unanchored issue at line {} (no codeSnippet)",
                        currentScope, astScope, issue.getLineNumber());
            } else if (currentScope == IssueScope.LINE && isAtScopeStart) {
                // Issue at the start of a scope — likely describes the whole scope
                issue.setIssueScope(astScope);
                log.debug("Upgraded scope from LINE to {} for issue at line {} (scope start)",
                        astScope, issue.getLineNumber());
            }
        }
    }

    private static IssueScope mapScopeKindToIssueScope(ScopeKind kind) {
        return switch (kind) {
            case BLOCK -> IssueScope.BLOCK;
            case FUNCTION -> IssueScope.FUNCTION;
            case CLASS, NAMESPACE, MODULE -> IssueScope.FILE;
        };
    }

    private static int scopeOrdinal(IssueScope scope) {
        return switch (scope) {
            case LINE -> 0;
            case BLOCK -> 1;
            case FUNCTION -> 2;
            case FILE -> 3;
        };
    }
}
