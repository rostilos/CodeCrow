# Index fact retention investigation

The reported RAG warnings on 2026-09-28 at 22:56:03 and 22:56:13 identify `plugin-index-output-limit` for JavaScript files in NovaPoshta/select2 and Jajuma/BFCache. The host's `GraphFactComposer` rejected entire facts when any field exceeded 4,096 characters. This was an indexing admission rule, not a SQLite restriction. The JavaScript plugin uses complete AST callee text for call targets, so an immediately invoked function can legitimately exceed that threshold.

## Implemented repair

`analysis-plugins/contracts/python/codecrow_plugins/graph_composition.py` now merges complete plugin file facts. It removes the string-length, serialized-byte, per-plugin fact-count, framework fact-count, and aggregate fact-count admission rules rather than raising their values. Semantic duplicates still merge contributor provenance, results remain deterministic, nested plugin paths are rebased, and invalid or failing optional contributors still produce recoverable diagnostics. `PluginRuntime` no longer exposes the removed graph-admission constants.

The structural store retains complete fact fields in SQLite TEXT. This change does not add facts wholesale to model prompts or change query-time relevance selection or pagination. Existing implementation fingerprints include the neutral plugin contract, so the corrected producer has a distinct content-derived identity. Previously discarded facts are not restored in already sealed indexes by a source edit.

No running service, configuration, database, or index was modified. No image was built or deployed and no benchmark or paid inference was run. The running images can continue emitting the old diagnostic until the user deploys the updated code and builds the relevant index.

## Regression evidence

Plugin regressions retain all strings beyond 4,096 characters, an aggregate payload exceeding 16 MiB, more than 5,000 language facts and 2,000 framework facts, and provenance from a late duplicate. A real JavaScript immediately invoked function preserves its exact long target. The RAG integration regression then runs real plugin composition and file indexing and verifies that exact target and source survive both ordinary and bulk SQLite ingestion.

The complete RAG suite passed 447 tests with zero failures, errors or skips; JUnit is `/tmp/codecrow-index-fact-rag-tests.xml`. The final graph-only plugin contract suite passed 393 tests with zero failures, errors or skips; JUnit is `/tmp/codecrow-graph-retention-contracts.xml`. The isolated Docs build passed with 150 pages, 6,274 anchors and nine changed-page links/fragments checked; the original 527-file dist tree was unchanged. Docs validation artifacts are under `/tmp/codecrow-index-retention-docs`. These offline checks establish data-retention behavior; they are not a paired benchmark or evidence of measured F1, latency or cost changes.

## Additional audit and pending scope

Other independent indexing ceilings remain in the current implementation:

- RAG source-file size (512 KiB by default), repository file count, and structural unit count.
- Repository composition symbol count (250,000) and architecture packet count (100,000).
- Detection marker contents (262,144 cumulative bytes / 4,096 files) and root/evidence selection (64 in Python and Java).
- Data-contract declaration/reference collection (2,048 each per file).
- Hyva call-state traversal (64), Magento inherited-plugin affected descendants (200), and Quarkus configuration facts (128).

Automatic approval review rejected the broader edit removing those limits because of resource-exhaustion/service-disruption risk and insufficient explicit authorization for that expanded scope. A user approval question is pending. The rejected root edit and detector/data-contract edit did not execute. An independently applied six-file framework change was saved as `/tmp/codecrow-index-framework-retention.patch` and reversed; all six paths were confirmed clean. The patch has not been reapplied. Operational concurrency controls, cancellation, timeouts, tenant boundaries, archive protections and explicit source-selection policies are not part of the proposed data-retention removal.
