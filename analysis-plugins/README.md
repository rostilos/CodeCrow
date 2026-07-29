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

Repository analysis is available to every selected analysis plugin.
JavaScript uses it to snapshot imports, component prop declarations, and JSX
usage, then resolves only unique relative module targets into exact
caller/component graph packets. Its typed validator rejects the former coarse
`javascript-file` proof class, uses exact presence relationships to refute
contradicted absence claims, and requires an exact defect fact for positive typed
approval. This behavior lives entirely under `languages/javascript`; generic RAG
and inference hosts only consume neutral snapshots, packets, and validation
results.

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

Per-file graph facts remain neutral chunk payload metadata. They are not copied
into every semantic chunk's embedded source text. After deterministic retrieval,
the inference host renders each complete fact line once per bounded prompt and
records only the prompt-visible facts as validation evidence. Focused repository
architecture packets retain their own exact text, attributes, paths, and packet
keys. This keeps embeddings source-specific without moving concrete plugin
interpretation into the RAG or inference hosts.

The `data-contracts` domain plugin activates only when a repository contains an
explicit contract path (`contract/`, `contracts/`, `schema/`, `schemas/`) or a
GraphQL, Protocol Buffers, or JSON Schema file. It records at most 256 sorted
field candidates per file and joins a contract declaration to exact quoted-key,
member-access, or field-declaration references in any language. A PR overlay
also emits a bounded removed-reference relation when a changed producer,
consumer, or contract no longer has a base-branch edge. Line-only moves do not
produce a removal. Both current and removed relations are navigation evidence:
they retrieve the contract and remaining consumers/tests, but cannot positively
validate a semantic defect. Dynamic/computed keys, aliases, informal prose
outside explicit contract locations, and external schemas remain unresolved
instead of being guessed. The plugin adds no model or embedding call.

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
`generated`, or `excluded`. Java applies it before changed-file enrichment; RAG
applies it before full/incremental file loading and PR overlay construction; and
inference maps generated/excluded files to explicit non-reviewable hunk
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
