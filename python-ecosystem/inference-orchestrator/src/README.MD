# Inference Orchestrator

The inference service consumes durable review jobs from Java, runs the review model, and returns findings with an explicit completion state. Interactive ask, summarize, and QA documentation commands have separate handlers.

## Review path

Java supplies the acquired diff, provider revisions, project identity, staged target tree, and proposed-file overlay. The reviewer parses every changed-text hunk and asks the RAG service to prepare a sealed graph of the proposed tree. A compatible target graph supports an incremental update; otherwise RAG builds the proposed-tree graph directly. The model starts after preparation succeeds.

The host maps changed hunks to graph units and groups hunks in the same unit or directly related units. Each group is reviewed with its diff, relevant graph relationships, and any exact reads requested by the model. Reads can query graph pages, structural units, proposed or target source lines, literal code search, and the acquired diff. Identical reads reuse their actual result. A group that makes no decision and obtains no new evidence ends as unresolved. There is no fixed model-turn or tool-call count.

A finding must have a changed-line anchor and explain a source-supported failure mechanism. The response is `complete` when all parsed hunks are decided, `partial` when some change scopes remain unresolved, and `error` when a valid review cannot run. Partial findings are retained. Java controls persistence, old-issue reconciliation, and provider publication according to that status.

The graph is required for repository review. An unavailable graph produces an incomplete result; it does not silently become a source-only clean review. Binary and unsupported diff sections remain unresolved. Ask, summarize, and QA documentation commands remain independent of this review path.

## Runtime

The HTTP service is built by `Dockerfile` or `Dockerfile.observable` and is normally started with the deployment Compose file. The RAG service URL is provided by `RAG_API_URL`; the internal queue uses `REDIS_URL`. Java provides model credentials per request. Repository paths and graph receipts are scoped to the project and the pinned review revision.

For local checks, run the Python tests in `../tests` with the service dependencies installed. The ten-PR public comparison is run separately through the benchmark harness; architecture checks alone do not establish precision, recall, F1, speed, or cost changes.
