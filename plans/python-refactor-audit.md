# Python refactoring audit

Scope: production Python in `python-ecosystem/` and `analysis-plugins/`, including
review, commands, QA generation, providers, HTTP/queue lifecycle, repository
indexing, graph storage/querying, parsing and plugin implementations.

The starting inventory contained 145 production modules and approximately
65,550 lines. The work splits responsibilities and removes repeated work; it does
not impose arbitrary file-length or class-length limits. Existing uncommitted
multi-stage review work was preserved. No application/plugin/API release trees
or production acceptance gates were added.

## Findings and implementation

| Area | Finding | Implemented response |
| --- | --- | --- |
| Structural storage | FTS retained a second source copy; composite primary keys duplicated auxiliary B-trees | External-content FTS and eligible `WITHOUT ROWID` tables for new graphs; old physical layouts remain readable and incrementally editable |
| Storage responsibilities | Generation ownership, write state, resolution, reconciliation, projection and receipt handling lived together | Explicit services under `core/structural_graph/`; original public import boundary retained |
| Read and seal work | Per-edge endpoint hydration caused repeated queries; manifest backfill retained all missing cache rows | Batched endpoint projections and streamed keyset backfill |
| Full/delta indexing | Duplicated file extraction, plugin finalization, state writing and publication | `GenerationBuilder`, `FileIndexer`, `RepositoryEnrichment`, `GenerationPublisher` and pending-build ownership; manager retains admission/public API |
| Index failure cleanup | Initialization/cancellation could abandon connections, pending ownership or unfinished plugin sessions | Unconditional resource ownership cleanup; optional cleanup reports warnings; rollback refreshes unit/relation counters |
| Proposed-tree context | One service owned preparation, attestation, navigation and evidence formatting | Composed generation, snapshot, source and evidence services; unused layered-reader implementation retired |
| Graph lifetime | Every sealed query re-read temporary overlay bytes | Graph queries use sealed receipt provenance; local source independently attests current bytes; tenant/revision/manifest binding remains enforced |
| Preparation concurrency | Requests with differing selection/source inputs could share one in-process preparation | Single-flight identity includes the complete preparation scope; concurrent profile regressions |
| Graph queries | Large mixed module and repeated serialization of growing traversal responses | Projection/source, lookup, walk, impact, context and response services; cached projections with exact incremental serialization accounting |
| AST splitter | Query/traversal collectors duplicated detail logic; inventory caps lost facts; IDs ignored content after 500 characters | Semantic extractor, AST details, record normalization and chunk emitter; complete distinct inventories and full-content IDs |
| Source fragments | Short oversized tails could be discarded; fallback line offsets repeatedly rescanned prefixes | Retain nonblank source fragments and accumulate fallback line positions |
| Local verifier source | Check-then-open paths allowed replacement races; wide directory scans retained excessive descriptors | Descriptor-bound no-follow reads/enumeration, nonblocking rejection of special files and depth-first directory ownership |
| RAG client | Transport lifetime and endpoint payloads were interleaved; malformed JSON objects were assumed | `RagTransport`, bindings and `ReviewQueries`; separate preparation/read pools; observable malformed-response fallback |
| Redis consumers | Review/command lifecycle duplicated; failed startup left state; one task per progress event | Shared consumer lifecycle and ordered single-task event drain; existing queue/result/TTL contracts retained |
| Commands and QA | Prompt/context/execution/result handling mixed; direct fallback could run twice; JSON repair could alter quoted source | Composed command services, single execution/fallback owner, independent QA records/formatting, shared string-aware JSON parsing |
| Provider adapters | Provider parameter shaping and client construction mixed | OpenAI-compatible adapter, parameter and Vertex modules behind existing factory |
| Provider sockets | Eager DNS validation did not bind the actual connection destination | Validate every new connection and dial accepted numeric addresses, preserving HTTP Host/TLS SNI; existing private-endpoint opt-in retained |
| HTTP lifetime | Startup/serving exceptions leaked resources; duplicate streams polled task completion | Cleanup on all lifespan exits; completion-driven shared NDJSON streaming; constant-time inference service-secret comparison |
| Dependency graph | Reused builders retained earlier request state; recursive traversal and repeated degree scans | Reset per build, iterative traversal, degree indexing and fail-open malformed optional relations |
| Magento | One repository resolver owned all domain analysis and repeatedly looked up sources/symbols | Explicit domain topology services, shared source/symbol index and typed intermediate evidence |
| PHP and Hyva | Parsing, joining and session state mixed; failed changed-file parsing retained stale evidence | Separate parser/index/resolver/session owners, path-indexed state, stale-path removal and worker cleanup |
| Neutral plugin runtime | Composition and session lifecycle mixed; skipped sessions were not closed | Independent graph composer/repository runtime; close on finish/timeout/abandonment, recoverable cleanup diagnostics |
| Import joining/catalog | Repeated repository scans for imports; installation roots could share module namespaces | One neutral import index per join, corrected Python package-relative/precedence behavior, root-specific package namespaces |
| Retired surfaces | Unused MCP pool, parser/classifier side channels and dangling review exports | Remove dead implementation and its obsolete-only tests; active error-envelope handling retains coverage |
| Documentation | Old embedding/Qdrant and removed pool settings contradicted the current implementation | Update existing developer pages and bilingual environment reference; preserve existing routes |

Generic hosts still depend only on neutral plugin contracts. Plugin snapshot wire
formats remain unchanged. Optional graph/plugin/source-enrichment failures are
observable and do not become new review acceptance barriers. Native AST traversal
restrictions and explicit API response coverage remain visible; missing graph
facts are not treated as proof of absence.

## Storage evidence

[Recorded measurements](python-refactor-storage-evidence.json) come from an
isolated copy of one existing sealed graph (2,621 units, 18,963 relations). No
live index was rewritten.

| Physical layout | SQLite bytes |
| --- | ---: |
| Existing sealed file, untouched | 70,852,608 |
| Existing layout, `VACUUM` applied | 66,043,904 |
| Compact layout, same `VACUUM` treatment | 52,465,664 |

The layout saves **20.56% against the equally compacted baseline**, or 25.95%
against the untouched file. All eleven common logical-table digests match.
Complete FTS ranking/result rows match for six sampled terms; FTS integrity and
foreign-key checks pass. Both compaction controls have zero freelist pages.
This is physical-layout evidence for one fixture, not a production-wide storage,
precision, recall, latency or paid-cost claim. New full generations benefit;
existing generations and clones retain their current supported physical layout.

Graph response tests compare complete old/new response hashes for 24 deterministic
fixtures across traversal strategies, detail levels, source inclusion and caller
budgets. A separate work-count test verifies each selected compact record is
serialized once. These establish output parity and avoided repeated work, not an
end-to-end latency improvement.

## Reviewed and intentionally retained

- Generic grammar loading/query caching, file selection, source attestation,
  generation tenant binding, mutation leases and HTTP stream ownership retain
  their established contracts. Changes address concrete resource/identity bugs.
- Smaller language/framework extractors remain stateless or already compose
  path-indexed repository sessions; no speculative rewrites were applied.
- MCP configuration, credential environments, runtime cancellation and telemetry,
  serialization and recursive-agent final-response reservation were reviewed;
  active contracts remain. No-tool structured requests now skip MCP inventory.
- QA HTTP routing and existing parser-native safety diagnostics remain. Large
  domain-specific topology services are separated by responsibility rather than
  split again merely to satisfy a line-count target.
- The review planner/discovery/synthesis/verifier contract from the preceding
  implementation remains intact. No model calls were added by this refactor.

## Verification

The integrated checks passed in isolated existing Python environments:

| Check | Result |
| --- | --- |
| Inference: `python -m pytest -p no:cacheprovider tests` | 756 passed |
| RAG: `python -m pytest -p no:cacheprovider tests` | 383 passed |
| Neutral contracts and all plugin families: `python -m pytest -p no:cacheprovider tests` | 392 passed |
| `python tools/validate_plugin_boundaries.py` | Passed |
| Production Python compilation and unresolved-global scan | 237 modules; no unresolved globals |
| Real package smoke imports | 73 RAG and 75 inference modules imported |
| Independent integration review | 38 focused binding, API, cleanup and identity tests passed; no defect found |
| Application and Docs `git diff --check` | Passed |
| Public Docs `npm run build` in isolated `/tmp` copy | Passed, including typecheck, client build, prerender and SEO checks |
| Docs internal links | 12 distinct links in changed MDX matched route constants and App routes; build verified 6,261 root-relative anchors across 150 canonical pages |

Inference emitted two dependency warnings: Starlette's deprecated AnyIO portal
alias and a Pydantic settings forward-reference warning for FastMCP lifespan.
The Docs build reported its existing stale Browserslist data. None failed checks.
Unused compatibility-only test suites were removed with their uncalled code;
passing-test counts therefore are not directly comparable with the old total.

The full RAG run includes source/tenant binding, full/delta proposed-tree oracles,
old/new storage-layout clone compatibility, traversal response parity, cleanup
failures and the additional source/profile regressions. The plugin suite was
rerun after its final module/theme ownership-index improvement. HTTPX/HTTPCore
transport tests exercise real transport interfaces with controlled sockets and
DNS; they do not call paid providers.

No services were rebuilt or redeployed, no live index was migrated, and no review
benchmark was run. Existing deployed indexes remain unchanged. Review quality,
production latency and paid model cost still require the user's paired run.
The provider transport's isolated HTTPX/HTTPCore pool adapter uses an internal
library seam; its tests and pinned dependencies must be checked when upgrading
those libraries.
