from __future__ import annotations

from pathlib import Path

import pytest

from codecrow_plugins import (
    CandidateClaim,
    FileArtifact,
    GraphFact,
    OutcomeStatus,
    PluginCatalog,
    PluginRuntime,
    ProjectSelector,
    RepositoryFacts,
    ValidationDecision,
)


PLUGINS_ROOT = Path(__file__).resolve().parents[3]


@pytest.fixture(scope="module")
def catalog() -> PluginCatalog:
    return PluginCatalog.discover(PLUGINS_ROOT)


@pytest.mark.parametrize(
    ("framework", "dependency", "source_path", "language"),
    (
        ("ember", "ember-source", "packages/web/app/router.js", "javascript"),
        ("express", "express", "packages/web/src/server.js", "javascript"),
        ("nextjs", "next", "packages/web/src/app/page.js", "javascript"),
        ("ember", "ember-source", "packages/web/app/router.ts", "typescript"),
        ("express", "express", "packages/web/src/server.ts", "typescript"),
        ("nextjs", "next", "packages/web/src/app/page.tsx", "tsx"),
    ),
)
def test_package_root_detection_supports_javascript_and_typescript_only_sources(
    catalog: PluginCatalog,
    framework: str,
    dependency: str,
    source_path: str,
    language: str,
):
    package_path = "packages/web/package.json"
    capabilities = ProjectSelector(catalog.registry).select(RepositoryFacts(
        revision="0123456789abcdef",
        paths=tuple(sorted((package_path, source_path))),
        marker_contents={
            package_path: f'{{"dependencies":{{"{dependency}":"latest"}}}}',
        },
    ))

    assert {"json", language, framework} <= set(capabilities.repository_plugins)
    assert capabilities.file_plugins[source_path] == (language,)
    assert f"root:packages/web" in capabilities.detection_evidence[framework]


def test_package_detection_does_not_join_source_roots(catalog: PluginCatalog):
    capabilities = ProjectSelector(catalog.registry).select(RepositoryFacts(
        revision="0123456789abcdef",
        paths=("one/package.json", "two/src/server.ts"),
        marker_contents={"one/package.json": '{"dependencies":{"express":"latest"}}'},
        source_root="two",
    ))

    assert "express" not in capabilities.repository_plugins


def test_package_detection_retains_every_matching_framework_root(
    catalog: PluginCatalog,
):
    capabilities = ProjectSelector(catalog.registry).select(RepositoryFacts(
        revision="0123456789abcdef",
        paths=(
            "apps/admin/package.json",
            "apps/admin/server.js",
            "apps/store/package.json",
            "apps/store/server.js",
        ),
        marker_contents={
            "apps/admin/package.json": '{"dependencies":{"express":"latest"}}',
            "apps/store/package.json": '{"dependencies":{"express":"latest"}}',
        },
    ))

    assert {"root:apps/admin", "root:apps/store"} <= set(
        capabilities.detection_evidence["express"]
    )
    runtime = PluginRuntime(catalog)
    for path in ("apps/admin/server.js", "apps/store/server.js"):
        facts, diagnostics = runtime.graph_facts(
            FileArtifact(
                path,
                "import express from 'express'; const app = express();",
            ),
            capabilities,
        )
        assert not diagnostics
        assert any(
            fact.kind == "express-application" and fact.path == path
            for fact in facts
        )


def test_ember_indexes_nested_routes_framework_roles_and_templates(catalog: PluginCatalog):
    plugin = catalog.implementation("ember")
    artifacts = (
        FileArtifact(
            "app/router.js",
            """import EmberRouter from '@ember/routing/router';
export default class Router extends EmberRouter {}
Router.map(function () {
  this.route('posts', { path: '/articles' }, function () {
    this.route('new');
  });
  this.route('admin', function () {
    this.route('users', { path: 'people' });
  });
});""",
        ),
        FileArtifact(
            "app/routes/posts.ts",
            """import Route from '@ember/routing/route';
import { service } from '@ember/service';
export default class PostsRoute extends Route {
  @service('session') currentUser;
}""",
        ),
        FileArtifact(
            "app/models/post.ts",
            """import Model, { belongsTo, hasMany } from '@ember-data/model';
export default class Post extends Model {
  @belongsTo('user') author;
  @hasMany('comment') comments;
}""",
        ),
        FileArtifact("app/templates/posts.hbs", "<PostList /> {{legacy-card}}"),
    )

    facts = {
        fact
        for artifact in artifacts
        for fact in plugin.index_file(artifact).value
    }

    nested = next(fact for fact in facts if fact.kind == "ember-route" and fact.target == "posts.new")
    assert dict(nested.attributes) == {"parent": "posts", "path": "/articles/new"}
    parent = next(fact for fact in facts if fact.kind == "ember-route" and fact.target == "admin")
    nested_option = next(
        fact for fact in facts if fact.kind == "ember-route" and fact.target == "admin.users"
    )
    assert dict(parent.attributes)["path"] == "/admin"
    assert dict(nested_option.attributes)["path"] == "/admin/people"
    assert any(
        fact.kind == "ember-service-injection"
        and fact.source == "posts"
        and fact.target == "session"
        for fact in facts
    )
    assert {
        (fact.relation, fact.target, dict(fact.attributes)["property"])
        for fact in facts
        if fact.kind == "ember-data-relationship"
    } == {
        ("belongs-to", "user", "author"),
        ("has-many", "comment", "comments"),
    }
    assert {
        fact.target for fact in facts if fact.kind == "ember-template-component"
    } == {"PostList", "legacy-card"}
    assert any(
        fact.kind == "ember-template-association"
        and fact.relation == "renders-route"
        and fact.target == "posts"
        for fact in facts
    )


def test_ember_text_fallbacks_ignore_comments_and_string_literals(catalog: PluginCatalog):
    plugin = catalog.implementation("ember")
    model_facts = plugin.index_file(FileArtifact(
        "app/models/post.ts",
        """export default class Post extends Model {
  // @belongsTo('user') author;
  note = "@hasMany('comment') comments";
}""",
    )).value
    template_facts = plugin.index_file(FileArtifact(
        "app/templates/posts.hbs",
        "{{!-- <CommentedCard /> --}} {{! {{old-commented}} }} <RealCard />",
    )).value

    assert not any(fact.kind == "ember-data-relationship" for fact in model_facts)
    assert {
        fact.target for fact in template_facts if fact.kind == "ember-template-component"
    } == {"RealCard"}


def test_ember_routes_require_the_exported_app_router_owner(catalog: PluginCatalog):
    plugin = catalog.implementation("ember")
    facts = plugin.index_file(FileArtifact(
        "app/router.js",
        """import EmberRouter from '@ember/routing/router';
export default class Router extends EmberRouter {}
Router.map(function () { this.route('real'); });
client.map(function () { this.route('fake'); });""",
    )).value

    assert {
        fact.target for fact in facts if fact.kind == "ember-route"
    } == {"real"}

    lookalike = plugin.index_file(FileArtifact(
        "app/router.js",
        "client.map(function () { this.route('fake'); });",
    ))
    assert lookalike.status is OutcomeStatus.ABSTAINED


def test_ember_service_and_model_macros_require_visible_proven_imports(
    catalog: PluginCatalog,
):
    plugin = catalog.implementation("ember")
    service_facts = plugin.index_file(FileArtifact(
        "app/routes/posts.ts",
        """import Route from '@ember/routing/route';
import { inject as useService } from '@ember/service';
export default class PostsRoute extends Route { @useService('session') currentUser; }
function lookalike(useService) {
  class FakeRoute { @useService('fake') fakeService; }
}""",
    )).value
    model_facts = plugin.index_file(FileArtifact(
        "app/models/post.ts",
        """import Model, { belongsTo as ownerOf } from '@ember-data/model';
export default class Post extends Model { @ownerOf('user') author; }
function lookalike(ownerOf) {
  class FakeModel { @ownerOf('fake') fakeOwner; }
}""",
    )).value
    unproven = plugin.index_file(FileArtifact(
        "app/services/cart.ts",
        "export default class Cart { @service('session') currentUser; }",
    )).value

    assert {
        fact.target for fact in service_facts if fact.kind == "ember-service-injection"
    } == {"session"}
    assert {
        fact.target for fact in model_facts if fact.kind == "ember-data-relationship"
    } == {"user"}
    assert not any(fact.kind == "ember-service-injection" for fact in unproven)


def test_express_indexes_routes_mounts_middleware_and_error_handlers(catalog: PluginCatalog):
    plugin = catalog.implementation("express")
    outcome = plugin.index_file(FileArtifact(
        "src/server.ts",
        """import express, { Router as CreateRouter } from 'express';
const app = express();
const router = CreateRouter();
const audit = (_req, _res, next) => next();
function failures(error, request, response, next) { next(error); }
router.route('/users/:id').get(audit, showUser).delete(deleteUser);
app.use('/api', router);
app.use(audit);
app.use(failures);""",
    ))

    assert outcome.status is OutcomeStatus.HANDLED
    facts = set(outcome.value)
    assert any(fact.kind == "express-application" and fact.target == "app" for fact in facts)
    assert any(fact.kind == "express-router" and fact.target == "router" for fact in facts)
    assert {
        fact.target for fact in facts if fact.kind == "express-route"
    } == {"DELETE /users/:id", "GET /users/:id"}
    assert any(
        fact.kind == "express-mount"
        and fact.source == "app"
        and fact.target == "router"
        and dict(fact.attributes)["mountPath"] == "/api"
        for fact in facts
    )
    assert any(fact.kind == "express-middleware" and fact.target == "audit" for fact in facts)
    assert any(fact.kind == "express-error-handler" and fact.target == "failures" for fact in facts)


def test_express_does_not_guess_that_unknown_path_middleware_is_a_router_mount(catalog: PluginCatalog):
    plugin = catalog.implementation("express")
    facts = plugin.index_file(FileArtifact(
        "server.js",
        """const express = require('express');
const app = express();
app.use('/private', authenticate);""",
    )).value

    assert any(fact.kind == "express-middleware" and fact.target == "authenticate" for fact in facts)
    assert not any(fact.kind == "express-mount" for fact in facts)


def test_express_does_not_treat_commented_imports_as_factory_evidence(catalog: PluginCatalog):
    plugin = catalog.implementation("express")
    outcome = plugin.index_file(FileArtifact(
        "server.ts",
        """// import express, { Router } from 'express';
const app = express();
app.get('/not-express-evidence', handler);""",
    ))

    assert outcome.status is OutcomeStatus.ABSTAINED


@pytest.mark.parametrize(
    "content",
    (
        """import express from 'express';
function build(express) {
  const app = express();
  app.get('/fake', handler);
}""",
        """import { Router } from 'express';
function build(Router) {
  const router = Router();
  router.get('/fake', handler);
}""",
    ),
)
def test_express_factories_respect_parameter_shadowing(
    catalog: PluginCatalog,
    content: str,
):
    outcome = catalog.implementation("express").index_file(FileArtifact("server.ts", content))

    assert outcome.status is OutcomeStatus.ABSTAINED


def test_express_suppresses_owner_facts_after_rebinding(catalog: PluginCatalog):
    outcome = catalog.implementation("express").index_file(FileArtifact(
        "server.ts",
        """import express from 'express';
let app = express();
app = fakeRouter;
app.get('/fake', handler);""",
    ))

    assert outcome.status is OutcomeStatus.ABSTAINED


def test_nextjs_indexes_both_routers_handlers_layouts_boundaries_and_loaders(catalog: PluginCatalog):
    plugin = catalog.implementation("nextjs")
    artifacts = (
        FileArtifact(
            "src/app/(shop)/products/[id]/page.tsx",
            """// A leading license or framework comment is not a statement.
'use strict';
'use client';
export default function ProductPage() { return <main />; }""",
        ),
        FileArtifact(
            "src/app/(shop)/products/layout.tsx",
            "export default function Layout({ children }) { return children; }",
        ),
        FileArtifact(
            "src/app/api/products/route.ts",
            """export async function GET() { return Response.json([]); }
export const POST = async () => new Response(null, { status: 201 });""",
        ),
        FileArtifact(
            "pages/blog/[slug].tsx",
            """export async function getServerSideProps() { return { props: {} }; }
export default function BlogPost() { return null; }""",
        ),
        FileArtifact(
            "pages/api/orders.ts",
            """export default function handler(req, res) {
  if (req.method === 'POST') res.end();
}""",
        ),
        FileArtifact(
            "src/middleware.ts",
            """export const config = { matcher: ['/account/:path*', '/admin/:path*'] };
export function middleware(request) { return NextResponse.next(); }""",
        ),
    )

    facts = {
        fact
        for artifact in artifacts
        for fact in plugin.index_file(artifact).value
    }

    assert any(
        fact.kind == "nextjs-page-route" and fact.target == "/products/[id]"
        and dict(fact.attributes)["router"] == "app"
        for fact in facts
    )
    assert any(
        fact.kind == "nextjs-layout" and fact.relation == "wraps" and fact.target == "/products"
        for fact in facts
    )
    assert any(fact.kind == "nextjs-client-boundary" for fact in facts)
    assert any(
        fact.kind == "nextjs-server-boundary"
        and fact.path.endswith("layout.tsx")
        for fact in facts
    )
    assert {
        fact.target for fact in facts
        if fact.kind == "nextjs-route-handler" and fact.path.endswith("route.ts")
    } == {"GET /api/products", "POST /api/products"}
    assert any(
        fact.kind == "nextjs-route-handler" and fact.target == "POST /api/orders"
        for fact in facts
    )
    assert any(
        fact.kind == "nextjs-data-loader" and fact.target == "getServerSideProps"
        and fact.source == "/blog/[slug]"
        for fact in facts
    )
    assert {
        fact.target for fact in facts if fact.kind == "nextjs-middleware"
    } == {"/account/:path*", "/admin/:path*"}


def test_nextjs_server_action_requires_a_function_directive_prologue(catalog: PluginCatalog):
    plugin = catalog.implementation("nextjs")
    facts = plugin.index_file(FileArtifact(
        "src/app/actions.ts",
        """export async function save() {
  // Comments may precede a directive.
  'use strict';
  'use server';
  await persist();
}
export async function notAnAction() {
  await inspect();
  'use server';
}""",
    )).value

    assert {
        fact.target for fact in facts if fact.kind == "nextjs-server-action"
    } == {"save"}


def test_nextjs_pages_api_method_checks_are_scoped_to_the_exported_handler(catalog: PluginCatalog):
    plugin = catalog.implementation("nextjs")
    facts = plugin.index_file(FileArtifact(
        "pages/api/items.ts",
        """function helper(req) {
  if (req.method === 'DELETE') return true;
}
export default function handler(req, res) { res.end(); }""",
    )).value

    assert {
        fact.target for fact in facts if fact.kind == "nextjs-route-handler"
    } == {"ANY /api/items"}


def test_nextjs_resolves_a_separately_exported_static_middleware_config(catalog: PluginCatalog):
    plugin = catalog.implementation("nextjs")
    facts = plugin.index_file(FileArtifact(
        "middleware.ts",
        """const config = { matcher: ['/private/:path*'] };
export { config };
export function middleware() {}""",
    )).value

    assert {
        fact.target for fact in facts if fact.kind == "nextjs-middleware"
    } == {"/private/:path*"}


def test_nextjs_http_exports_are_exact_bindings_not_incidental_names(catalog: PluginCatalog):
    plugin = catalog.implementation("nextjs")
    facts = plugin.index_file(FileArtifact(
        "src/app/api/items/route.ts",
        """const read = async () => Response.json([]);
export { read as GET };
export const description = 'POST';
export function helper() { const DELETE = 'not exported'; return DELETE; }""",
    )).value

    assert {
        fact.target for fact in facts if fact.kind == "nextjs-route-handler"
    } == {"GET /api/items"}


@pytest.mark.parametrize(
    ("path", "expected_route"),
    (
        ("pages/help.tsx", "/help"),
        ("src/pages/help.tsx", "/help"),
        ("app/help/page.tsx", "/help"),
        ("src/app/help/page.tsx", "/help"),
    ),
)
def test_nextjs_route_roots_are_anchored_but_supported_roots_remain_exact(
    catalog: PluginCatalog,
    path: str,
    expected_route: str,
):
    facts = catalog.implementation("nextjs").index_file(FileArtifact(
        path,
        "export default function Page() { return null; }",
    )).value

    assert any(
        fact.kind == "nextjs-page-route" and fact.target == expected_route
        for fact in facts
    )


def test_nextjs_ignores_nested_lookalike_roots_and_private_app_segments(
    catalog: PluginCatalog,
):
    plugin = catalog.implementation("nextjs")
    for path in ("docs/pages/help.tsx", "src/app/_private/demo/page.tsx"):
        outcome = plugin.index_file(FileArtifact(
            path,
            "export default function Page() { return null; }",
        ))
        assert outcome.status is OutcomeStatus.ABSTAINED


@pytest.mark.parametrize(
    ("path", "content"),
    (
        ("app/help/page.tsx", "export function Page() { return null; }"),
        ("app/help/layout.tsx", "export function Layout({ children }) { return children; }"),
        ("pages/help.tsx", "export function Page() { return null; }"),
        ("pages/api/items.ts", "export function handler(req, res) { res.end(); }"),
        ("app/api/items/route.ts", "export type GET = () => Response;"),
        ("app/api/items/route.ts", "type GET = () => Response; export { type GET };"),
        ("app/api/items/route.ts", "export const GET = 'not a function';"),
        ("middleware.ts", "export const config = { matcher: '/private' };"),
    ),
)
def test_nextjs_requires_runtime_exports_for_file_system_facts(
    catalog: PluginCatalog,
    path: str,
    content: str,
):
    outcome = catalog.implementation("nextjs").index_file(FileArtifact(path, content))

    assert outcome.status is OutcomeStatus.ABSTAINED


@pytest.mark.parametrize(
    ("plugin_id", "path", "content", "diagnostic"),
    (
        ("ember", "app/router.js", "Router.map(function( {", "ember-script-syntax-error"),
        ("express", "src/server.ts", "const app = express(;", "express-script-syntax-error"),
        ("nextjs", "src/app/page.tsx", "export default function Page( {", "nextjs-script-syntax-error"),
    ),
)
def test_framework_indexers_do_not_emit_partial_facts_from_error_trees(
    catalog: PluginCatalog,
    plugin_id: str,
    path: str,
    content: str,
    diagnostic: str,
):
    outcome = catalog.implementation(plugin_id).index_file(FileArtifact(path, content))

    assert outcome.status is OutcomeStatus.FAILED
    assert outcome.value is None
    assert outcome.diagnostic.code == diagnostic
    assert outcome.diagnostic.recoverable is True


@pytest.mark.parametrize(
    ("plugin_id", "path", "fact", "absence_message"),
    (
        (
            "ember",
            "app/router.js",
            GraphFact("ember-route", "Router", "declares", "posts", "app/router.js", 2),
            "The Router does not declare the posts route.",
        ),
        (
            "express",
            "src/server.js",
            GraphFact("express-route", "application", "handles", "GET /health", "src/server.js", 4),
            "The application does not handle GET /health.",
        ),
        (
            "nextjs",
            "src/app/products/page.tsx",
            GraphFact("nextjs-page-route", "page module", "defines", "/products", "src/app/products/page.tsx", 1),
            "The page module does not define /products.",
        ),
    ),
)
def test_framework_validation_rejects_contradicted_absence_but_never_treats_topology_as_defect_proof(
    catalog: PluginCatalog,
    plugin_id: str,
    path: str,
    fact: GraphFact,
    absence_message: str,
):
    plugin = catalog.implementation(plugin_id)
    contradicted = plugin.validate(CandidateClaim(
        category="bug-risk",
        path=path,
        line=fact.line,
        message=absence_message,
        evidence=(fact,),
        claim_kind=fact.kind,
    ))
    topology_only = plugin.validate(CandidateClaim(
        category="bug-risk",
        path=path,
        line=fact.line,
        message=f"{fact.source} has framework topology related to {fact.target}.",
        evidence=(fact,),
        claim_kind=fact.kind,
    ))

    assert contradicted.value.decision is ValidationDecision.REJECT
    assert topology_only.value.decision is ValidationDecision.INSUFFICIENT_EVIDENCE


@pytest.mark.parametrize(
    ("plugin_id", "path", "fact", "message"),
    (
        (
            "ember",
            "app/templates/admin.hbs",
            GraphFact(
                "ember-template-component", "app/templates/admin.hbs", "invokes",
                "AdminPanel", "app/templates/admin.hbs", 1,
            ),
            "AdminPanel is not rendered when account data is missing.",
        ),
        (
            "express",
            "src/server.js",
            GraphFact(
                "express-route", "application", "handles", "GET /health",
                "src/server.js", 4,
            ),
            "GET /health is not handled when authentication fails.",
        ),
        (
            "nextjs",
            "app/api/items/route.ts",
            GraphFact(
                "nextjs-route-handler", "/api/items", "handles", "GET /api/items",
                "app/api/items/route.ts", 1,
            ),
            "GET /api/items is not handled when authentication fails.",
        ),
    ),
)
def test_framework_validation_does_not_confuse_unsafe_behavior_with_absence(
    catalog: PluginCatalog,
    plugin_id: str,
    path: str,
    fact: GraphFact,
    message: str,
):
    outcome = catalog.implementation(plugin_id).validate(CandidateClaim(
        category="bug-risk",
        path=path,
        line=fact.line,
        message=message,
        evidence=(fact,),
        claim_kind=fact.kind,
    ))

    assert outcome.value.decision is ValidationDecision.INSUFFICIENT_EVIDENCE
    assert "absence" not in outcome.value.code


@pytest.mark.parametrize(
    ("plugin_id", "path", "category", "fact"),
    (
        (
            "ember", "app/router.js", "ember-invented-kind",
            GraphFact("ember-route", "Router", "declares", "posts", "app/router.js", 1),
        ),
        (
            "express", "src/server.js", "express-invented-kind",
            GraphFact(
                "express-route", "application", "handles", "GET /health",
                "src/server.js", 1,
            ),
        ),
        (
            "nextjs", "app/page.tsx", "nextjs-invented-kind",
            GraphFact("nextjs-page-route", "app/page.tsx", "defines", "/", "app/page.tsx", 1),
        ),
    ),
)
def test_framework_validation_does_not_treat_unknown_prefixed_categories_as_umbrella(
    catalog: PluginCatalog,
    plugin_id: str,
    path: str,
    category: str,
    fact: GraphFact,
):
    outcome = catalog.implementation(plugin_id).validate(CandidateClaim(
        category=category,
        path=path,
        line=1,
        message=f"{fact.target} is missing.",
        evidence=(fact,),
    ))

    assert outcome.value.decision is ValidationDecision.INSUFFICIENT_EVIDENCE
    assert outcome.value.code == f"{plugin_id}-unknown-fact-kind"


def test_express_validation_does_not_match_short_identifier_inside_an_unrelated_word(
    catalog: PluginCatalog,
):
    plugin = catalog.implementation("express")
    fact = GraphFact("express-application", "server.js", "declares", "app", "server.js", 1)

    outcome = plugin.validate(CandidateClaim(
        category="bug-risk",
        path="server.js",
        line=1,
        message="The wrapper does not declare a server factory.",
        evidence=(fact,),
        claim_kind="express-application",
    ))

    assert outcome.value.decision is ValidationDecision.INSUFFICIENT_EVIDENCE


def test_ember_conventional_template_lookup_does_not_prove_the_template_file_exists(
    catalog: PluginCatalog,
):
    plugin = catalog.implementation("ember")
    fact = GraphFact(
        "ember-template-association",
        "posts",
        "uses-conventional-template",
        "posts",
        "app/routes/posts.ts",
        1,
        (("ownerKind", "route"),),
    )

    outcome = plugin.validate(CandidateClaim(
        category="bug-risk",
        path="app/routes/posts.ts",
        line=1,
        message="The posts route is missing template file posts.hbs.",
        evidence=(fact,),
        claim_kind="ember-template-association",
    ))

    assert outcome.value.decision is ValidationDecision.INSUFFICIENT_EVIDENCE


@pytest.mark.parametrize(
    ("plugin_id", "path", "kind"),
    (
        ("ember", "app/router.ts", "ember-framework"),
        ("express", "src/server.ts", "express-framework"),
        ("nextjs", "src/app/page.tsx", "nextjs-framework"),
    ),
)
def test_framework_review_requests_exact_evidence(
    catalog: PluginCatalog,
    plugin_id: str,
    path: str,
    kind: str,
):
    contribution = catalog.implementation(plugin_id).review((path,)).value

    assert [request.kind for request in contribution.evidence_requests] == [kind]
    assert contribution.evidence_requests[0].identifier == path
    assert any("topology" in rule.casefold() for rule in contribution.rules)
