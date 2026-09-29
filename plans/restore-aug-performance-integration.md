# Restored review with source-branch indexing and capacity improvements

## Provenance

The branch `feature/restore-aug-50pr-20260929-auto` starts from the recovered
August checkout, not the later MCP-only review implementation.

- Original common ancestor: `f76b81f9edb63467dcec61b8bc1acf98373dc9e8`.
- Exact restored application baseline: `5e4e0641b6b50b67ad50ebe31cc6952c54407d25`,
  directly parented by that ancestor. Its 2,081 application files were checked
  byte-for-byte and by executable mode against `codecrow-public-restore-aug`.
- Exact restored frontend baseline: `2a4ea9988c8d007977fed9558ab6ee4ab5452186`,
  parent `20219d4e130cf27977cb2b337266b416237e6c55`. All 238 frontend files
  matched the recovered frontend.
- Performance source: `feature/mcp-graphfirst-20-20260929-03`, pinned at
  `656eb9c941bcc93bbf8966a0852fc89078dd2d9f`.

The source lineage from the common ancestor is `d0e8d6ca`, `8dd96da2`,
`dd696c83`, `a4ef0e01`, `80f45daf`, `656eb9c9`. Later commits mix performance
work with a review rewrite, so a merge or wholesale cherry-pick would not meet
the requested scope. The baseline is a separate commit; the performance and
cleanup change is reviewable against that exact baseline.

The source notes `python-refactor-audit.md` and
`indexing-core-investigation-2026-09-29.md` are retained verbatim from the pinned
source commit as historical performance evidence. Any descriptions of its
review engine are historical, not the integrated runtime's contract.

## Selected implementation

| Area | Source behavior retained or adapted |
| --- | --- |
| SQLite | External-content FTS, compact keyed tables, batched endpoint projection, keyset backfill and buffered relation ingestion with deferred derived lookups |
| Index extraction | Full definitions and fallback source, cached newline coordinates, producer-only representation fingerprint |
| Build workers | Isolated spawned full/delta/preparation workers, shared bounded capacity, cancellation, coalescing, progress and per-worker recovery |
| Graph retrieval | Source service decomposition, compact response assembly, exact binding, coverage, continuation and explicit response budgets |
| Plugins | Neutral runtime composition, import joining, scoped language/framework services and complete emitted graph facts with merged provenance |
| Java admission | Dedicated review/index/stream pools, default 16 review/index workers, durable index overflow and scheduled retry |
| Seed handoff | Ordered exact/active seed candidates, separate indexing policy, actual accepted seed revision/receipt propagated into host and MCP reads |
| Inference capacity | Source fair per-invocation scheduler, independent model/source/preparation admission and separate RAG HTTP pools |
| Runtime lifetime | Shared Redis consumer lifecycle, ordered events, idle stream heartbeats, cancellation/join and partial-startup cleanup |

Optional seed lookup and graph enrichment degrade with diagnostics. Tenant,
project, revision and sealed-generation checks remain enforced. Index policy
survives absence or rejection of a reusable seed. The accepted seed may differ
from the logical target; source tools still use the pinned target snapshot.

Source scheduling is wrapped around the restored provider calls. A neutral MCP
admission callback applies the shared source capacity before the existing
operation deadline. Command clients retain their default middleware behavior.
Source preparation and query HTTP pools are independent. Explicit lower local
worker values remain effective; changing source defaults does not alter live
containers.

## Review functionality preserved

The following remain the restored implementation: Stage 0 planning, Stage 1 file
review and relation briefings, candidate verification, optional Stage 2
cross-file analysis, Stage 3 aggregation/MCP verification, deduplication,
historical issue reconciliation, task history, prompt dry runs and quality
capture. Java's response validation, persistence and publication are unchanged.

A final invariant audit found 117 protected files byte-identical to the restored
baseline: prompt constants/builders, provider implementation and policy, capture
and dry-run logic, candidate ledger, Java analysis/reconciliation/status classes
and database migrations. Eleven scheduler-only modules also have identical ASTs
after removing the added scheduler imports and invocation wrappers. Mixed
seed/preparation/MCP wiring changes are covered by real payload-chain tests.

The newer review planner, MCP-only mode, structural-case verifier, provider
streaming rewrite, wire-capture system and partial-result persistence changes
were deliberately excluded. No new quality or latency result is asserted.

## Whole-tree cleanup

The source inventory covered the application, frontend submodule, plugins, Java,
Python, deployment/CI, CLI tools, tests, fixtures and notices. Before cleanup it
contained 2,416 files; afterward it contains 2,368 source files before these three
audit notes. Generated targets, caches, archives, local environments and private
configuration are excluded from source control.

- Python: import/relative-import analysis and repository-wide reference checks
  identified the unused JVM pool, parser/classifier/signature wrappers,
  import-time inspector and RAG revision wrapper. The parser's only live error
  envelope was moved to the source-equivalent shared sanitizer with its tests.
  Agent exports now use the canonical package; response-text tests target the
  canonical helper. All 475 retained Python files parsed successfully.
- Newer RAG whole-file/search endpoints had no consumer in this restored engine
  and were omitted with their exclusive DTOs/facade methods/tests. Graph,
  structural-unit, source-window, receipt and topology coverage remains.
- Java: 821 production files were scanned together with resources and command
  references. Seven unreferenced classes and four exclusive test classes were
  removed: obsolete DTOs, empty reconciliation/initializer placeholders, the
  unused HTML formatter and old default-quality-gate factory. Spring/JPA
  discovery, module descriptors, SPI and annotation contracts were retained.
- Frontend: TypeScript AST imports, exports and literal dynamic imports were
  resolved from `src/main.tsx`. Twenty-four unreachable modules and ten exclusive
  dependencies were removed. All 187 remaining scripts are reachable, with no
  unresolved edges or nonliteral dynamic imports. Active project creation/import
  routes retain their existing components. The lockfile changes remove those
  dependencies without an unrelated upgrade.
- Plugins: manifest entrypoints, syntax resources and Java SPI are discovery
  roots. Shared fixtures remain contract evidence. The existing boundary checker
  verifies that hosts do not import concrete language/framework implementations.
- Tools and build: standalone CLI modules, benchmark materialization/evaluation,
  coverage utilities, schemas and fixtures remain supported entrypoints even
  when they have no production import. CI shell references and the plugin
  assembler were checked. No unrelated benchmark corpus, source history or
  runtime data was classified as dead application code.

The audit is evidence-based reference and reachability analysis, not a claim
that static analysis can prove every dynamic runtime path unused. Only concrete
unused modules were removed. Existing composition boundaries were preferred to
additional generic abstraction layers.

## Deployment and verification

`deployment/build/production-build.sh --build-only` runs the full production
verification path: isolated Python matrices, Maven `clean verify`, plugin
assembly and all five Docker images. It loads local images without restarting
services. Omitting the flag also starts the local public Compose stack. The
restored benchmark stack and original restore checkout remain independent.

Private current configuration is copied locally, including temperature `0.6`;
credentials and `.env` files are not committed. The build uses a dedicated
`CODECROW_CI_VENV_ROOT` so it does not clear another run's Python environments.

Public Docs are reconciled to the restored stages plus selected performance
behavior. Existing Docs edits were backed up before targeted reconciliation.
`npm run build`, using command-local production URL defaults, passed for 150
canonical pages and 6,268 internal links. No website was published.

The production entrypoint completed successfully on 2026-09-30 (Europe/Kyiv):

| Verification | Result |
| --- | --- |
| Java reactor | 3,488 tests; zero failures, errors or skips |
| Python/plugin/tool matrices | 2,588 tests; zero failures, errors or skips |
| Plugin assembly | 29 Java plugins, deterministic order |
| Docker images | Web Server, Pipeline Agent, Inference Orchestrator, RAG Pipeline and Web Frontend built and loaded |
| Public Docs | Build, prerender and internal-link verification passed |
| Source boundaries | Plugin boundary checker, protected-file identity and scheduler AST checks passed |

Python totals comprise 393 plugin contracts, 445 RAG unit, 34 RAG integration,
1,540 inference unit, 51 inference integration and 125 review-tool tests. The
frontend production image includes its TypeScript/Vite build. Images were built
from the tested working tree; final commits additionally record this audit.

No new benchmark is part of this integration. The existing benchmark and its
restore checkout remain independent. Fixed-corpus paired evidence is required
before claiming F1, precision, recall, paid-cost or latency improvement.
