package org.rostilos.codecrow.astparser;

import org.junit.jupiter.api.AfterAll;
import org.junit.jupiter.api.BeforeAll;
import org.junit.jupiter.api.Test;
import org.rostilos.codecrow.astparser.model.*;

import java.util.List;
import java.util.Optional;

import static org.assertj.core.api.Assertions.assertThat;

/**
 * Integration tests for the AST parser facade.
 * Tests end-to-end parsing, scope resolution, and symbol extraction
 * using real tree-sitter grammars.
 */
class AstParserFacadeTest {

    private static AstParserFacade facade;

    @BeforeAll
    static void setUp() {
        facade = AstParserFacade.createDefault();
    }

    @AfterAll
    static void tearDown() {
        facade.close();
    }

    // ── Java parsing ─────────────────────────────────────────────────────

    private static final String JAVA_SOURCE = """
            package com.example.service;
            
            import java.util.List;
            import java.util.Optional;
            
            public class UserService {
            
                private final UserRepository repo;
            
                public UserService(UserRepository repo) {
                    this.repo = repo;
                }
            
                public Optional<User> findById(Long id) {
                    if (id == null) {
                        return Optional.empty();
                    }
                    return repo.findById(id);
                }
            
                public List<User> findAll() {
                    return repo.findAll();
                }
            
                public void deleteUser(Long id) {
                    if (id != null) {
                        repo.deleteById(id);
                    }
                }
            }
            """;

    @Test
    void java_parses_successfully() {
        try (ParsedTree tree = facade.parse("UserService.java", JAVA_SOURCE)) {
            assertThat(tree).isNotNull();
            assertThat(tree.getLanguage()).isEqualTo(SupportedLanguage.JAVA);
            assertThat(tree.getRootNode()).isNotNull();
        }
    }

    @Test
    void java_resolves_class_scope() {
        List<ScopeInfo> scopes = facade.resolveAllScopes("UserService.java", JAVA_SOURCE);
        assertThat(scopes).isNotEmpty();

        // Should find the class
        Optional<ScopeInfo> classScope = scopes.stream()
                .filter(s -> s.kind() == ScopeKind.CLASS && "UserService".equals(s.name()))
                .findFirst();
        assertThat(classScope).isPresent();
        assertThat(classScope.get().startLine()).isEqualTo(6);
    }

    @Test
    void java_resolves_method_scopes() {
        List<ScopeInfo> scopes = facade.resolveAllScopes("UserService.java", JAVA_SOURCE);

        List<String> methodNames = scopes.stream()
                .filter(s -> s.kind() == ScopeKind.FUNCTION)
                .map(ScopeInfo::name)
                .toList();
        assertThat(methodNames).contains("UserService", "findById", "findAll", "deleteUser");
    }

    @Test
    void java_resolves_innermost_scope_at_line() {
        // Line 16 is inside the if-block inside findById method
        Optional<ScopeInfo> scope = facade.resolveInnermostScope("UserService.java", JAVA_SOURCE, 16);
        assertThat(scope).isPresent();
        // Should be either the if-block or the findById method
        assertThat(scope.get().kind()).isIn(ScopeKind.BLOCK, ScopeKind.FUNCTION);
    }

    @Test
    void java_extracts_symbols() {
        SymbolInfo symbols = facade.extractSymbols("UserService.java", JAVA_SOURCE);
        assertThat(symbols.imports()).isNotEmpty();
        assertThat(symbols.classes()).contains("UserService");
        assertThat(symbols.functions()).contains("findById", "findAll", "deleteUser");
        assertThat(symbols.namespace()).contains("com.example.service");
    }

    // ── Python parsing ───────────────────────────────────────────────────

    private static final String PYTHON_SOURCE = """
            import os
            from typing import List, Optional
            
            class UserService:
                def __init__(self, repo):
                    self.repo = repo
            
                def find_by_id(self, user_id: int) -> Optional[dict]:
                    if user_id is None:
                        return None
                    return self.repo.find_by_id(user_id)
            
                def find_all(self) -> List[dict]:
                    return self.repo.find_all()
            
            def main():
                service = UserService(None)
                users = service.find_all()
                for user in users:
                    print(user)
            """;

    @Test
    void python_parses_successfully() {
        try (ParsedTree tree = facade.parse("service.py", PYTHON_SOURCE)) {
            assertThat(tree).isNotNull();
            assertThat(tree.getLanguage()).isEqualTo(SupportedLanguage.PYTHON);
        }
    }

    @Test
    void python_resolves_class_and_functions() {
        List<ScopeInfo> scopes = facade.resolveAllScopes("service.py", PYTHON_SOURCE);
        assertThat(scopes).isNotEmpty();

        assertThat(scopes.stream().anyMatch(s ->
                s.kind() == ScopeKind.CLASS && "UserService".equals(s.name())
        )).isTrue();

        List<String> funcNames = scopes.stream()
                .filter(s -> s.kind() == ScopeKind.FUNCTION)
                .map(ScopeInfo::name)
                .toList();
        assertThat(funcNames).contains("__init__", "find_by_id", "find_all", "main");
    }

    @Test
    void python_extracts_symbols() {
        SymbolInfo symbols = facade.extractSymbols("service.py", PYTHON_SOURCE);
        assertThat(symbols.imports()).hasSize(2);
        assertThat(symbols.classes()).contains("UserService");
        assertThat(symbols.functions()).contains("__init__", "find_by_id", "find_all", "main");
    }

    // ── JavaScript parsing ───────────────────────────────────────────────

    private static final String JS_SOURCE = """
            const express = require('express');
            
            class Router {
                constructor(app) {
                    this.app = app;
                }
            
                registerRoutes() {
                    this.app.get('/users', (req, res) => {
                        if (req.query.id) {
                            return res.json({ id: req.query.id });
                        }
                        res.json([]);
                    });
                }
            }
            
            function createApp() {
                const app = express();
                const router = new Router(app);
                router.registerRoutes();
                return app;
            }
            
            module.exports = { createApp };
            """;

    @Test
    void javascript_parses_and_resolves_scopes() {
        List<ScopeInfo> scopes = facade.resolveAllScopes("router.js", JS_SOURCE);
        assertThat(scopes).isNotEmpty();

        // Should find at least the Router class and createApp function
        boolean hasClass = scopes.stream().anyMatch(s -> s.kind() == ScopeKind.CLASS);
        boolean hasFunction = scopes.stream().anyMatch(s -> s.kind() == ScopeKind.FUNCTION);
        assertThat(hasClass).isTrue();
        assertThat(hasFunction).isTrue();
    }

    // ── PHP parsing ────────────────────────────────────────────────────

    private static final String PHP_SOURCE = """
            <?php
            namespace App\\Models;

            class Block {
                private string $name;
                private array $attributes;

                public function __construct(string $name, array $attributes = []) {
                    $this->name = $name;
                    $this->attributes = $attributes;
                }

                public function render(): string {
                    if (empty($this->attributes)) {
                        return "<{$this->name}/>";
                    }
                    $attrs = array_map(function($key, $value) {
                        return "$key=\\"$value\\"";
                    }, array_keys($this->attributes), $this->attributes);
                    return "<{$this->name} " . implode(' ', $attrs) . "/>";
                }
            }
            """;

    @Test
    void php_parses_successfully() {
        try (ParsedTree tree = facade.parse("Block.php", PHP_SOURCE)) {
            assertThat(tree).isNotNull();
            assertThat(tree.getLanguage()).isEqualTo(SupportedLanguage.PHP);
        }
    }

    @Test
    void php_resolves_class_and_methods() {
        List<ScopeInfo> scopes = facade.resolveAllScopes("Block.php", PHP_SOURCE);
        assertThat(scopes).isNotEmpty();

        // Should find the Block class
        assertThat(scopes.stream().anyMatch(s ->
                s.kind() == ScopeKind.CLASS && "Block".equals(s.name())
        )).isTrue();

        // Should find methods
        List<String> funcNames = scopes.stream()
                .filter(s -> s.kind() == ScopeKind.FUNCTION)
                .map(ScopeInfo::name)
                .toList();
        assertThat(funcNames).contains("__construct", "render");
    }

    @Test
    void php_resolves_innermost_scope_in_method() {
        // Line inside render() method
        Optional<ScopeInfo> scope = facade.resolveInnermostScope("Block.php", PHP_SOURCE, 15);
        assertThat(scope).isPresent();
        assertThat(scope.get().kind()).isIn(ScopeKind.BLOCK, ScopeKind.FUNCTION);
    }

    @Test
    void php_resolves_anonymous_function() {
        List<ScopeInfo> scopes = facade.resolveAllScopes("Block.php", PHP_SOURCE);
        // The array_map callback is an anonymous function — it may have
        // an empty name or no separate @name capture, so count FUNCTION scopes
        // that are NOT named methods (__construct, render).
        long totalFunctions = scopes.stream()
                .filter(s -> s.kind() == ScopeKind.FUNCTION)
                .count();
        // __construct + render + at least 1 anonymous = 3+
        assertThat(totalFunctions).isGreaterThanOrEqualTo(3);
    }

    // ── Scope-aware hashing ──────────────────────────────────────────────

    @Test
    void scope_hash_changes_when_scope_body_changes() {
        Optional<ScopeInfo> scope1 = facade.resolveInnermostScope("UserService.java", JAVA_SOURCE, 14);
        assertThat(scope1).isPresent();
        String hash1 = facade.computeScopeHash(JAVA_SOURCE, scope1.get());

        String modifiedSource = JAVA_SOURCE.replace(
                "return repo.findById(id);",
                "return repo.findById(id).filter(User::isActive);"
        );
        Optional<ScopeInfo> scope2 = facade.resolveInnermostScope("UserService.java", modifiedSource, 14);
        assertThat(scope2).isPresent();
        String hash2 = facade.computeScopeHash(modifiedSource, scope2.get());

        assertThat(hash1).isNotEqualTo(hash2);
    }

    @Test
    void scope_hash_stable_when_other_scope_changes() {
        // Get hash for findAll method
        List<ScopeInfo> scopes1 = facade.resolveAllScopes("UserService.java", JAVA_SOURCE);
        Optional<ScopeInfo> findAll1 = scopes1.stream()
                .filter(s -> s.kind() == ScopeKind.FUNCTION && "findAll".equals(s.name()))
                .findFirst();
        assertThat(findAll1).isPresent();
        String hash1 = facade.computeScopeHash(JAVA_SOURCE, findAll1.get());

        // Modify deleteUser method (different scope)
        String modifiedSource = JAVA_SOURCE.replace(
                "repo.deleteById(id);",
                "repo.deleteById(id);\n            log.info(\"Deleted\");"
        );

        List<ScopeInfo> scopes2 = facade.resolveAllScopes("UserService.java", modifiedSource);
        Optional<ScopeInfo> findAll2 = scopes2.stream()
                .filter(s -> s.kind() == ScopeKind.FUNCTION && "findAll".equals(s.name()))
                .findFirst();
        assertThat(findAll2).isPresent();
        String hash2 = facade.computeScopeHash(modifiedSource, findAll2.get());

        // findAll method unchanged → hash should be stable
        assertThat(hash1).isEqualTo(hash2);
    }

    // ── Edge cases ───────────────────────────────────────────────────────

    @Test
    void unsupported_file_returns_empty() {
        assertThat(facade.isSupported("README.md")).isFalse();
        assertThat(facade.extractSymbols("README.md", "# Hello")).isEqualTo(SymbolInfo.empty());
        assertThat(facade.resolveAllScopes("README.md", "# Hello")).isEmpty();
    }

    @Test
    void empty_source_produces_tree() {
        try (ParsedTree tree = facade.parse("empty.java", "")) {
            assertThat(tree).isNotNull();
        }
    }

    @Test
    void malformed_source_still_produces_tree() {
        String broken = """
                public class Broken {
                    public void foo( {
                        // missing closing paren
                    }
                """;
        try (ParsedTree tree = facade.parse("Broken.java", broken)) {
            assertThat(tree).isNotNull();
            assertThat(tree.hasErrors()).isTrue(); // tree-sitter marks errors
        }
    }

    @Test
    void scope_chain_returns_innermost_first() {
        // Line inside if-block inside findById inside UserService
        List<ScopeInfo> chain = facade.resolveScopeChain("UserService.java", JAVA_SOURCE, 16);
        assertThat(chain).isNotEmpty();
        // First should be narrower scope (if-block or method), last should be broader (class)
        if (chain.size() > 1) {
            assertThat(chain.get(0).lineSpan()).isLessThanOrEqualTo(chain.get(chain.size() - 1).lineSpan());
        }
    }
}
