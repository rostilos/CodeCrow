# CodeCrow Plugins

This bounded context contains neutral extension contracts and independently owned
language, framework, and cross-language domain implementations. It is intentionally separate from the
generic Java and Python hosts: hosts depend only on contracts, accept an empty
plugin registry, and retain deterministic fallback behavior.

The production distribution is assembled explicitly from local build artifacts.
It does not download or hot-load plugin code. Descriptors intentionally have no
release or compatibility field.

Directory ownership:

- `contracts/`: runtime-neutral descriptor schema, shared fixtures, and matching
  Java/Python host APIs;
- `languages/`: language-owned parsing, indexing, context, planning, validation,
  grammar, and query resources;
- `frameworks/`: framework-owned contributions that declare their language
  dependencies;
- `domains/`: language-neutral repository relationships such as explicit
  cross-language data contracts;
- `fixtures/`: durable plugin/context quality fixtures shared by the host gates.

Production Python images copy this tree and discover descriptors and owned
resources at startup. Each Java implementation builds one shaded plugin JAR.
`tools/assemble_java_plugins.py` verifies every declared Java entry point has
exactly one artifact and copies it into the pipeline agent's runtime plugin
directory. The application POM contains no concrete plugin dependency; the
distribution loads assembled JARs through Spring Boot's external loader path.
Missing or duplicate declared artifacts fail assembly. An empty installed set is
valid for the host, while an invalid partial dependency graph fails discovery.

Descriptors intentionally have no application release, API, schema, or
compatibility version fields. Selection and composition are deterministic, plugin
contributions are bounded, and a plugin can explicitly handle, abstain, or fail.
Plugins cannot call model-provider APIs, so they add no model calls and preserve
BYOK behavior.

The neutral Python `SyntaxContribution` lets a selected language plugin declare
its tree-sitter module and factory, plugin-owned RAG query resource, built-in tags
availability, and rich-traversal safety. Python, Java, JavaScript, TypeScript, Go,
and PHP provide this declaration. The RAG host consumes it without importing a
concrete implementation or deriving a plugin ID from a language name. If no
selected plugin contributes syntax, the existing generic splitter remains the
fallback. If selected syntax cannot yield an AST chunk, fallback text splitting
does not apply the generic host's language-specific regex metadata.

The Python pnpm graph/index plugin reads default and named dependency catalogs
from each detected `pnpm-workspace.yaml` root. It emits `configures-module` facts
with the exact package, version expression, source line and YAML pointer, without
claiming installation or ownership of member-package dependencies. Malformed YAML
is an observable recoverable plugin failure; unsupported entries contribute no
facts. Its parser uses the existing PyYAML dependency.

Babel has a Python graph/index contributor for literal configured plugin/preset
references in JavaScript and TypeScript. A root or nested package manifest naming
`@babel/core` or `@vitejs/plugin-react` activates it across the selected source
root, so a sibling package can use a workspace dependency. Each call still needs
source-proven loader bindings. It recognizes direct transform options and React's
`babel` options through static imports or awaited literal dynamic imports,
including immutable local paths from Node's imported `createRequire().resolve()`.
Type assertions supply no runtime provenance, and ambiguous, mutated or out-of-scope
bindings are excluded. `configures-module` preserves the configured string and source line;
it does not resolve installed packages, normalize Babel shorthand, or evaluate
dynamic configurations. Parser feature names are excluded. It contributes no
Babel review rules or validation verdicts.

The Python `wrangler` domain plugin reads explicit `assets.directory` declarations
from `wrangler.json`, `wrangler.jsonc`, and `wrangler.toml`, at the repository root
or in nested packages. It records each top-level or named-environment declaration
as an unresolved `declares-directory` fact, with configuration-file provenance,
a configuration pointer, the literal spelling, and its configuration-relative
normalized path. The file-level origin is line 1, not an inferred field location.
The existing JSONC decoder is shared with the TypeScript configuration plugin.

Directory declarations do not prove that generated files exist. They do not bind
to a same-named source file, select an active environment, apply CLI overrides,
follow generated-configuration redirects, or run a build. Invalid configuration
produces a recoverable diagnostic; source analysis remains available. Ordinary
index creation includes the declarations; existing sealed generations are not
rewritten. This is structural context support, not a measured quality claim.
See [Cloudflare assets configuration](https://developers.cloudflare.com/workers/static-assets/binding/).

The Python `dockerfile` domain plugin records literal `FROM` base-image declarations
in `Dockerfile`, `Dockerfile.*` and `*.Dockerfile` files at any depth, excluding
`.dockerignore` files. Each `configures-module` fact retains its image spelling
(including a tag or digest) and the first instruction line. Images remain unresolved
module references with `targetResolutionProven=false`, even if a local file has
the same name. Repeated declarations retain their separate source locations.

The extractor respects platform flags, case-insensitive instructions and stage
references, escape directives, continued instructions and RUN/COPY/ADD heredoc
bodies. Earlier named stages, `scratch` and dynamic image expressions contribute
no external-image fact. It does not evaluate ARG defaults, resolve named build
contexts, inspect COPY/RUN image references, contact registries or execute builds.
Unterminated continuations/heredocs and unsupported FROM syntax produce a recoverable
plugin diagnostic; other analysis remains available. New indexes include these
facts; existing sealed generations are not rewritten. This is structural support,
not a measured quality improvement. See the [Dockerfile reference](https://docs.docker.com/reference/dockerfile/).

The Python `github-actions` domain plugin reads external repository action and
reusable-workflow references in root `.github/workflows/*.yml` and `*.yaml` files.
It is independent of the repository language. Job-level `uses`, action-step
`uses`, literal job/service container images and `docker://` action images
contribute `configures-module` facts. Container declarations retain the exact
image name, tag or digest without resolving a registry. The original
`targetSpecifier`, declaration kind, source line and YAML pointer remain available;
repository actions also retain `gitRef`. `targetResolutionProven=false` prevents
a same-named local unit from being treated as external code. No repository or
image is fetched, and no ref, installation or runtime behavior is verified.
Local/self references, expressions, empty images and unrelated keys are omitted. Malformed, duplicate or merged YAML mappings
produce a recoverable plugin diagnostic; other analysis remains available. These
facts enter newly built indexes and do not rewrite existing generations. No model
calls or review verdicts are added. Reference syntax follows the
[GitHub workflow syntax](https://docs.github.com/en/actions/reference/workflows-and-actions/workflow-syntax).

The Python `localization` graph/index plugin recognizes `lingui.config.*` and
`lunaria.config.*` in JavaScript projects, including TypeScript configuration files.
It records literal catalog `include`/`exclude`, Lingui `path`, and Lunaria `pattern`
declarations as `declares-file-pattern` facts with source lines and the original
`targetSpecifier`. Static ESM object exports, a local const used only by its export,
and an imported `defineConfig` wrapper are supported. The default Lingui
`<rootDir>` token is anchored to the configuration directory; explicit rootDir,
process-CWD-relative paths, locale placeholders and Lunaria parameters remain
unresolved. No glob is expanded and no generated file or translation is inferred.
Dynamic, ambiguous, spread or CommonJS configuration forms contribute no pattern
facts; malformed syntax emits a recoverable plugin diagnostic. Existing indexes
are not rewritten. The facts appear when the configuration is normally parsed by
an index build. This adds no model calls, review rules or validation verdicts.

Astro has a Python-only graph/index plugin for component-script imports. It
retains exact module identifiers and source lines for static imports, re-exports,
and literal dynamic imports. Interpolated dynamic-import templates are retained
separately as exact `imports-pattern` facts without evaluating their substitutions.
It does not declare syntax/chunking, resolve package members or parse the template
body; those paths retain generic source fallback.
Malformed scripts produce recoverable diagnostics. No new model calls are made.

Repository analysis is available to every selected analysis plugin.
JavaScript uses it to snapshot imports, component prop declarations, and JSX
usage, then resolves only unique relative module targets into exact
caller/component graph packets. Its typed validator rejects the former coarse
`javascript-file` proof class, uses exact presence relationships to refute
contradicted absence claims, and requires an exact defect fact for positive typed
approval. This behavior lives entirely under `languages/javascript`; generic RAG
and inference hosts only consume neutral snapshots, packets, and validation
results.

JavaScript repository snapshots also retain package manifests and file inventory
for unconditional `exports` aliases. Exact and wildcard aliases point only to
present files, preserve exclusions and declaration evidence, and do not assert
runtime module resolution. TypeScript records leading triple-slash `types`
references as source-bound type dependencies. Hosts consume these neutral facts;
language-specific extraction stays in the plugins. Supported export wildcards also
retain exact `declares-file-pattern` declarations, separately from file inventory
and concrete aliases. The TypeScript contributor reads local `compilerOptions.types`
from `tsconfig.json` and `tsconfig.*.json` as `configures-module` facts, using JSONC
comments/trailing commas and exact JSON pointers. It does not resolve inherited
configuration or installed packages; malformed configuration is an observable,
recoverable enrichment failure.

Repository implementation responsibilities are separated behind the existing
neutral contracts. The runtime capability gateway delegates per-file graph
composition and streaming repository lifecycle to dedicated services. Magento
uses an indexed source/symbol lookup and composed DI, event, route, layout,
frontend, configuration, queue, and data topology services. Shared layout and
configuration evidence is passed explicitly; domain services do not receive the
orchestrator. PHP separates AST parsing, declaration extraction, receiver
resolution, reference extraction, and cross-file relation joining. Hyva separates
template extraction, dependency lookup, topology resolution, and snapshot state.
The existing repository snapshot representations are unchanged.

Incremental PHP symbols are indexed by source path so updating one source does
not scan every symbol. Failed changed-file parsing removes that source's old
symbols and releases parsing workers; unaffected files remain available. Hyva
likewise removes old template metadata before replacing a changed source.
Repository finalization closes every session that offers `close()`, including
sessions skipped after a deadline; callers can also close an abandoned handle.
Cleanup failures produce recoverable `plugin-repository-close-exception`
diagnostics. A malformed optional per-file graph contribution produces
`plugin-index-invalid-result` and does not remove other plugins' valid evidence.

The neutral `ImportRecordIndex` builds path, module, and exported-name lookups once
per import-graph join. Java and Python resolve through those lookups while owning
all namespace and package semantics. Python resolves relative imports from a
package initializer within that package, prefers a `.py` source over a colocated
stub, and prefers a package initializer over a same-name source module. Ambiguous
remaining candidates stay unresolved. These are deterministic architecture
checks, not evidence of a review precision, recall, or latency improvement.

Framework-owned indexing is also packaged inside this boundary. Django extracts
AppConfig, installed apps, middleware and root URL configuration, URL paths and
includes, views, models/relations, middleware hooks, and signal receivers from
Python. Quarkus extracts CDI beans/injection, JAX-RS resources/routes,
configuration-property uses and application-property key/profile metadata,
schedules, reactive channels, and Panache topology from Java. Ember.js extracts
router maps, route/controller/component/service/model roles, service injection,
Ember Data relationships, and conservative `.hbs` ownership/invocations;
Express.js extracts routing and
middleware topology, and Next.js extracts file-system routes, boundaries, data
loaders, and Server Actions from JavaScript-family source; Rails extracts routes,
mounts, controllers/actions, models, associations, callbacks, and Active Job
queue/perform/retry/discard declarations from Ruby. These plugins use bounded repository
markers and exact source constructs, and abstain when a construct is dynamic or
ambiguous. Their structural facts can refute
a matching absence claim, but topology alone is not positive proof of a defect.
Selection may under-detect when optional marker acquisition is bounded or
unavailable. Each branch refresh uses the retained pinned-head path/evidence
projection and reports partial detection when the 64-root/evidence ceiling is
reached. A new immutable generation is published only after its admitted
projection is sealed.

Graph-fact attributes whose keys start with
`retrievalIdentifier:` are a neutral exact-retrieval hint. The value nominates
an identifier inside a fact's already-proven related paths; generic RAG may
prioritize a chunk with that `primary_name` before other chunks from the same
path. Hosts do not interpret the suffix or assign language semantics. PHP uses
this for statically named instance/static target methods. Exact instance
receivers may come from one typed `$this` property, one declared method
parameter, a local alias/`new` assignment whose preceding assignments all agree
on one target, or a direct `new` expression. The fact records that resolution
source without making the host interpret it. Union/intersection, conflicting,
unknown-factory, and other dynamic receivers contribute no hint. A call-return
chain is resolved only when every intermediate method has one exact
in-repository, non-null named return type (or a directly declared `self` type);
nullable, union/intersection, `static`, external, ambiguous, inherited-`self`
subclass assumptions, and undeclared returns remain unknown. The fact records
each intermediate declaring type/method/return contract and uses
`receiverResolution=exact-call-return`.
When that uniquely resolved target directly declares the method, the PHP-owned
fact also carries its exact declared visibility, modifiers, and
namespace-resolved return type. If the target does not override the method, the
plugin follows a unique in-repository parent-class chain and includes the exact
parent declaration path and contract. External/ambiguous parents, inherited
private methods, trait/interface/magic dispatch, and absent return declarations
stay unknown; the generic host only transports the attributes.

Per-file graph facts remain dedicated neutral records. They are not copied into
source units. The structural index stores their attributes, paths, and packet
keys as graph metadata; Stage 1 receives only a bounded relation map and can open
an exact unit or file through its repository tools. This keeps source and graph
records separate without moving concrete plugin interpretation into the index or
inference hosts.

The neutral graph composer preserves every distinct emitted fact and complete
fact strings, merging semantic duplicates and contributor provenance. The
structural index stores these facts and their resolved edges in the immutable
target-head generation. Each selected review contributor receives all owned paths
in the requested scope; the runtime retains all distinct rules, evidence requests
and path groups. Plugin prompt rendering groups repeated evidence descriptions
without a separate character cutoff. Stage-level prompt sizing and packing still
apply, and exact-evidence visibility controls typed claims. Contributor failures
remain observable optional diagnostics while source review continues.

The `data-contracts` domain plugin activates only when a repository contains an
explicit contract path (`contract/`, `contracts/`, `schema/`, `schemas/`) or a
GraphQL, Protocol Buffers, or JSON Schema file. It records at most 256 sorted
field candidates per file and joins a contract declaration to exact quoted-key,
member-access, or field-declaration references in any language. Its target-head
relations are navigation evidence: they locate the contract and consumers/tests,
but cannot positively validate a behavioral defect. PR changes remain
authoritative in the supplied diff and current-file evidence; no mutable PR
overlay is persisted. Dynamic/computed keys, aliases, informal prose outside
explicit contract locations, and external schemas remain unresolved instead of
being guessed.

PHP and Magento apply the same proof distinction at their own plugin boundary.
Namespaces, inheritance, DI preferences, plugins, observers, routes, ACLs,
generated types, and other effective relationships are structural context; their
presence can contradict an absence claim but cannot by itself approve a defect.
Magento also extracts direct `window.<name>` definitions and calls from JavaScript
inside PHTML `<script>` elements. It creates a caller-to-definition edge only when
both templates are referenced by the same concrete layout XML source and exactly
one co-declared template defines that global. Dynamic property names, plain reads,
repository-wide name matches, separate layout sources, and multiple definitions
remain unresolved. The edge retrieves the defining template as bounded exact
context; it is topology rather than defect proof and adds no model call.
Magento price-pool enrichment joins three exact inputs: a PHP literal
`PRICE_CODE`, a `getPrice(Class::PRICE_CODE)` argument resolved by the PHP AST,
and the effective `Magento\Catalog\Pricing\Price\Pool` `prices` item in `di.xml`.
The resulting packet relates the consumer, configuration source, and registered
price model. A dynamic constant/value, a different call, a mismatched pool key,
an unknown class, or an ambiguous symbol abstains. Identical global registrations
are emitted once rather than repeated for every Magento area; a real
area-specific override retains its own fact. This is runtime topology, not a
rule that assumes `getPrice()` is safe or unsafe.

Hyva is a separate framework plugin that requires Magento. It is selected only
when bounded repository evidence contains `Hyva_Theme` in `app/etc/config.php` or
the Hyva theme-module Composer package. It resolves a PHTML ViewModel only when an
exact `ViewModelRegistry` annotation, import, and `require(Class::class)`
assignment agree. A literal `fetch()` route produced by that ViewModel is joined
to Magento's exact Web API service and DI implementation, then to PHP-owned call
relations. Direct `$this->method()` calls are PHP-owned intra-class edges; Hyva
does not reparse PHP. The route also relates only a layout sibling subtree that
reads an Alpine state identifier written by the fetch continuation. Unknown
registries, unimported short classes, dynamic routes, ambiguous layouts, missing
state writes, and unresolved Web API/DI/call targets abstain. Traversal is capped
at 64 call states and emits topology, not a static defect checklist or model call.
Magento facts for relationships that are themselves invalid or inapplicable use
the neutral graph attribute `semanticRole=diagnostic`. The host prioritizes that
role inside the existing bounded prompt and the Magento validator decides which
exact fact kinds can positively approve a typed claim. Generic hosts contain no
PHP/Magento fact table and add no model call.

The neutral file-policy capability returns `full`, `architecture-only`,
`generated`, or `excluded`. Java applies it before changed-file enrichment; the
structural index applies it before immutable generation loading; and inference
maps generated/excluded files to explicit non-reviewable hunk
dispositions before Stage 0. Empty registries keep the generic fallback, while a
failed policy contribution aborts instead of silently changing the disposition.

Magento classifies XML as module configuration only when it is directly below an
`etc` directory or directly below a known Magento area such as `etc/frontend`.
Custom descendants such as `etc/samples/*.xml` stay `full` project content and
are handled by the generic indexing path. DTDs and entities in those ordinary
documents are not resolved by the Magento repository analyzer. Actual Magento
configuration XML remains architecture input, but a malformed file or one with a
DTD/entity declaration is quarantined from the architecture snapshot instead of
failing the repository index. Other valid plugin inputs continue to contribute
deterministic context. Runtime/plugin contract failures remain fatal.

The optional `composer-audit` domain plugin implements the neutral Java
`ReviewCheckPlugin` contract. A fresh PR result can receive deterministic security
findings in separate `ReviewCheckResult`/`ReviewCheckFinding` entities after
ordinary issue persistence, without importing a concrete contributor into the
host. Checks share only mapped finding data with `CodeAnalysisIssue`; normal
issue queries, tracking and comments never load advisories. One VCS summary
links to the dedicated Dependency security section, and quality gates explicitly
combine ordinary issue and check findings. Changed Composer roots are compared at pinned base/head revisions and a
separate, capability-dropped CLI container executes a checksum-verified
`composer.phar`. It has no application volumes or credentials and blocks private
network egress. Missing plugins/workers, malformed auth or inputs, timeouts and
unreachable repositories leave core review available with diagnostics. The
project's `reviewChecks` map can disable a contributor; Composer audit defaults
to enabled for matching changed markers. It adds no model calls. Full ownership,
selection and failure details live in the public developer plugin architecture
and configuration pages.
