# Indexing core investigation — 2026-09-29

The rebuilt services still spent minutes preparing an optional review graph before making a model call. This investigation separates that indexing work from inference scheduling and measures the extraction/storage paths with fixed offline fixtures. It does not establish an end-to-end latency or review-quality improvement.

## Observed runtime and affected job

The inspected services started on 2026-09-28 at 21:27 UTC. Fresh logs and database records were collected at 21:42–21:48 UTC. Seven selected inference modules and six selected RAG modules matched the then-current deployed source SHA-256 exactly; this was the rebuilt runtime, not the older service image.

The active Garden request was **PR 5**, durable job **10288**, inference job `dce1f308-359d-4d81-92b7-4e30c3f068aa`. Two PR 7 commands after deployment, jobs 10286 and 10287, returned cached analysis 812 and completed in 5.86 and 2.57 seconds. They did not dispatch another inference review.

| Garden PR 5 event | UTC timestamp |
| --- | --- |
| Java dispatch to inference | 21:31:39.998 |
| Inference acknowledgment | 21:31:40.542 |
| Graph preparation event | 21:31:40.624 |
| Existing seed reported unavailable | 21:31:43.876 |
| Proposed-tree full-index lease acquired | 21:34:30.785 |
| Graph ready | 21:44:24.494 |
| Cross-file synthesis | 21:44:44.705 |
| Six verifier cases started | 21:44:49.704 |
| Inference completed | 21:46:41.752 |

The graph preparation permit was acquired with **0 ms queue wait**. About **12 minutes 44 seconds** elapsed in graph preparation. No model request occurred during that wait. Once the graph was ready, discovery and synthesis ran before the six independent verifier cases began. The remaining model and verifier work completed about **2 minutes 17 seconds** after graph readiness.

Eight branch builds were scheduled together after startup because reconciliation detected a repository-index representation change. Six completed in 132–1092 seconds of active time by the inspection; two remained active around 1174 seconds. Garden branch build 10281 ran from 21:28:11.290 to 21:41:04.251, producing 2544 documents and 15112 units reported as chunks. A later duplicate Garden branch request completed without another full build after the first generation became available.

The branch loader selected **2546 files**, while the proposed-tree full fallback selected **10409 files**. Missing or unusable seed data was allowing the fallback to lose the host's project selection/profile. This is distinct from waiting for a concurrency permit.

Effective capacities were inference admission 16 with inherited model-call capacity 16; Java actions 16, index dispatch 10, maintenance 10 and MVC streams 26; RAG full/delta capacity 40 across one API worker. The RAG workload was active despite available capacity. More admission slots alone did not remove extraction and SQLite contention.

A 15-second live `py-spy` sample placed about **38%** of top frames in SQLite relation insertion and **7%** in commit; receipt construction/sealing accounted for about **17%** of inclusive samples. These categories are not an additive attribution. The single RAG API process was using about **230% CPU** while several builds overlapped. Its `/proc` counters recorded **30,038,130,688 physical bytes written** over roughly 16 minutes of service lifetime. This counter includes the service workload as a whole and cannot be assigned to one PR. The sample is retained in `/tmp/codecrow-index-profile/rag-live.json`.

Private audit artifacts: `/tmp/codecrow-runtime-audit-20260928T214246Z/`, including `summary.md`, `index-jobs.json`, source hashes and whitelist-only capacity settings. No provider payload or private reasoning was inspected for these conclusions. Interleaved plugin logs were not assigned to projects without correlation.

## Source extraction and graph scope changes

- Selected semantic definitions retain complete source and structural metadata. Character-size fragmentation and overlapping fragments are removed; unknown-language or failed-syntax fallback retains one complete source unit. Existing constructor/configuration inputs remain accepted without fragmenting structural output. Parser selection, parser thresholds and simplified navigation companions retain their established behavior.
- Query-capture byte positions use one newline-offset index per source file. This removes repeated prefix scans while preserving Unicode, CRLF and end-of-file positions.
- Java sends ordered exact-target and active seed candidates, and independently sends the authoritative indexing policy. RAG validates each candidate against tenant, branch, revision, manifest, representation and requested selection/profile. An unavailable exact candidate can be skipped in favor of a compatible active generation.
- A full fallback retains the supplied include/exclude patterns, project type and source root. Existing flat seed coordinates remain accepted. Optional candidate/policy metadata is normalized; malformed or partial entries are ignored with a warning while valid candidates remain available. This does not weaken checks on a seed actually reused. Later queries carry the actual accepted sealed seed triple, not an earlier failed candidate. The logical target snapshot remains unchanged.
- The representation identity covers persisted-source extractors, producers, schema, sealing code, producer collaborators, dependencies and runtime settings. Query/review orchestration or reader/manager facade edits alone no longer invalidate all sealed repositories. Actual producer changes can still require a rebuild.

The implementation continues to treat graph context as optional. Unavailable enrichment remains observable and the acquired diff/local source can still support review. The changes do not weaken tenant or source-revision binding.

## Paired extraction fixture

`/tmp/codecrow-structural-extraction-profile.py` generates one fixed 10,005-line, 381,953-character source file with 1,000 methods. Paired artifacts are `codecrow-structural-extraction-before.json`, `codecrow-structural-extraction-after.json` and `codecrow-structural-extraction-after-coordinate-index.json` under `/tmp`.

| Implementation | Extraction seconds | Stored units | Fragments | Complete class units |
| --- | ---: | ---: | ---: | ---: |
| Deployed extractor | 6.192 | 1059 | 59 | 0 |
| Complete-unit renderer | 5.637 | 1002 | 0 | 1 |
| Renderer plus newline index | 1.501 | 1002 | 0 | 1 |

The fixture changes structural unit boundaries intentionally; it is not a claim that old and new extracted graphs are byte-identical. The source is preserved, and separate tests cover complete large class/method ownership, relationships near the end of a definition, exact source and FTS retrieval, and complete fallback text. This one extraction timing is neither a production estimate nor a precision/recall measurement.

## Storage implementation and paired fixture

Fresh full-build file ingestion batches relation SQL and auxiliary ownership writes. Duplicate edges retain contributor unions and already resolved endpoints. Batches bound SQL parameter arrays, not graph content. Every pending fact is flushed before repository finalization.

Private full builds defer secondary lookup indexes and external-content FTS until source ingestion completes. `building_lookup_indexes` identifies this stage before repository finalization and relation resolution. Canonical tables, source, aliases and foreign keys remain authoritative while building. FTS unavailability retains the existing indexed name/path fallback. Incremental clones continue to use their established lookup structures and reconciliation path. Journal mode and cache size are not changed merely because experimental alternatives were measured below.

The diagnostic uses the deployed `StructuralGraphWriter`, endpoint resolution, full receipt creation, seal, publication and bound reopen. Each graph contains 1500 complete source units and 30000 relations. Buffer experiments replace only `relation_buffer.py`, `relations.py`, `writer.py` and `write_state.py` with the recorded source snapshot. Deferred-index experiments defer eleven relation lookup indexes; the production full-build path also controls its other derived lookup structures.

| Variant | Total build s | Relation ingestion s | Logical writes GB | Peak WAL MB | Peak journal MB | Final DB MB |
| --- | ---: | ---: | ---: | ---: | ---: | ---: |
| `wal_default` | 10.428 | 8.665 | 4.129 | 92.737 | 0.000 | 91.877 |
| `delete_default` | 8.883 | 7.289 | 3.680 | 0.000 | 0.125 | 91.877 |
| `wal_32m` | 7.937 | 6.170 | 1.207 | 92.737 | 0.000 | 91.877 |
| `wal_deferred` | 6.999 | 4.880 | 1.518 | 89.198 | 0.000 | 88.175 |
| `wal_deferred_fts` | 7.062 | 4.947 | 1.511 | 89.223 | 0.000 | 88.166 |
| `wal_buffered` | 9.208 | 7.336 | 4.968 | 92.762 | 0.000 | 91.902 |
| `wal_buffered_deferred_fts` | 5.714 | 3.616 | 1.935 | 89.235 | 0.000 | 88.179 |

All seven variants produce identical complete receipts, all ten canonical-table digests/counts, sampled exact source and sampled FTS search results. FTS integrity, foreign-key checks, SQLite integrity, publication and bound reopen pass. The common generation manifest is `61090683dc5d7f6786957f3109af0737451810bab9fe8dcb8ace989d2c7e0afb`. The fixture has no repository snapshot rows; repository-finalizer reconciliation is covered separately by implementation tests.

**Measurement limit:** `/tmp` is tmpfs. `wchar` measures logical writes issued by the process; peak journal/WAL values are file lengths, not cumulative rewrites. Kernel physical-write counters are cancelled or near zero, so this fixture cannot establish physical-device I/O savings. These are single diagnostic observations with other host work present. Derived rebuilding, resolution, receipt and seal time are included; exhaustive post-build verification is excluded from total build time.

## Thread/process concurrency control

Four copies of the same 300-unit, 4000-relation graph were compared. A barrier after sealing prevents validation from overlapping unfinished builds in the clean threaded comparison. Sequential sums come from the corresponding four runs of the same fixture.

| Variant | Sequential build sum s | Four-thread build makespan s | Build CPU s | Voluntary context switches |
| --- | ---: | ---: | ---: | ---: |
| `wal_default` | 4.017 | 6.495 | 13.449 | 777,799 |
| `wal_buffered` | 2.968 | 3.285 | 6.591 | 401,163 |
| `wal_buffered_deferred_fts` | 2.568 | 3.231 | 5.847 | 386,332 |

Four separate processes using the unchanged deployed writer completed the full cohort, including validation, in **1.702 seconds**. Each build took **1.027–1.173 seconds**. Logical tables, receipts, source and search results remained identical.

The buffer reduced the measured thread overhead, but four threads did not achieve a parallel speedup in this fixture. The separate-process control supports isolating CPU-heavy builds; it does not establish production throughput, process cancellation correctness or memory requirements. Earlier `cohorts/report*.json` totals include exhaustive digest/search verification; use `build-only-cohorts/*/summary.json` for build-only comparisons.

Scripts, frozen sources and per-run artifacts are retained in `/tmp/codecrow-sqlite-storage-diagnostic/`; `report.md` records details and limitations. No live generation was rewritten by these experiments.

## Build-process isolation

Each RAG API process owns lifecycle-managed independent spawned build workers, with one reusable single-process executor per admitted slot. `RAG_FULL_INDEX_CONCURRENCY` supplies the shared capacity for JSON and streaming full/delta indexing and proposed-tree preparation; the default is 16, and an explicit value such as the audited runtime's 40 remains effective. Children start on demand and initialize process-local managers, plugin clients and runtime resources from serialized configuration. Recently used slots are reused first, retaining warm workers across operations. Sealed graph queries remain in the API host. Additional Uvicorn workers and replicas multiply build pools and memory requirements.

The parent sends plain operation data and forwards coarse progress through a manager-hosted queue, owned by one additional IPC server process per API host. Progress-transport failure degrades with a diagnostic while the authoritative result future remains independent; frequent source/SQL cancellation checks use shared memory without a per-check IPC request. Streaming coordinators retain the acquired source snapshot while children work. Shared-memory cancellation flags feed existing file and SQL checkpoints; cancellation joins admitted child cleanup before releasing source ownership. Parent-side preparation single-flight retains the complete existing identity: a remaining waiter keeps the operation alive, and cancelling its last waiter stops the shared work. Shutdown signals and drains workers before closing the parent manager.

Child HTTP failures retain their public status and detail. A crashed child fails its own operation while other workers continue; the parent joins that worker before source cleanup, and a later request can recreate its executor. There is no automatic replay of partially executed operations. Offline crash regression testing exposed a race in multiworker executor failure notification. A standalone standard-library reproduction without CodeCrow or a progress queue left both the crashed and surviving workers’ futures pending until an additional executor wakeup; then both became broken and the executor joined. `/tmp/codecrow-process-sentinel-probe.py` and its `.txt` output retain that evidence. This motivated independent single-process executors using public APIs, without a private executor patch, version gate or added build timeout. It was an offline-test finding, not an observed production incident. The final targeted lifecycle/integration run passes 13 tests, including isolated crash/recreation, joined cancellation, transferred-source stream disconnect, concurrent retirement, ten distinct full/delta child PIDs with responsive parent exact queries, and a real preparation seed/policy roundtrip. Parent logs report actual process-slot `queue_wait_ms` separately from `execution_ms` and outcome. This process lifecycle leaves the durable Java job and recovery contract in place. Process isolation addresses the demonstrated interpreter contention but does not establish production throughput or an appropriate memory budget for every repository.

## Verification and operational boundary

Focused source/storage tests exercise full source preservation, coordinate equivalence, buffered duplicates across batches, contributor/endpoint preservation, receipt/search equality, derived-index construction and repository-finalizer aborts. The complete offline inference suite passes **888 tests** (two existing third-party warnings); the targeted Java policy/seed suite passes **26 tests**. The complete RAG suite passes **445 tests**, with no failures, errors or skips, including the 13 process-worker lifecycle/integration tests. JUnit results are retained at `/tmp/codecrow-index-profile/rag-final.xml` and `/tmp/codecrow-index-profile/inference-final.xml`.

The public Docs build passes in the isolated checkout `/tmp/codecrow-indexing-docs-fi394kgk`: 150 canonical pages and 6271 root-relative anchors. All 19 edited pages match the built source; 28 internal links pass. The original `dist` tree remains unchanged. Build and link artifacts are retained under `/tmp/codecrow-sqlite-storage-diagnostic/`. Both repository whitespace checks pass.

No service rebuild/restart, live configuration edit, paid inference replay, review benchmark or judge run was performed by this investigation. The inspected running jobs belong to the user's execution. A new end-to-end paired run is still required before claiming a product latency, cost or F1 change.
