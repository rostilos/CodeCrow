#!/usr/bin/env python3
"""Build and audit deterministic Magento architecture context for a repository."""

from __future__ import annotations

import argparse
import json
import logging
import sys
import time
from collections import Counter
from pathlib import Path


PROJECT_ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(
    0,
    str(PROJECT_ROOT / "analysis-plugins" / "contracts" / "python"),
)

from codecrow_plugins import (  # noqa: E402
    FileArtifact,
    PluginCatalog,
    PluginRuntime,
    ProjectSelector,
    RepositoryFacts,
)
def _candidate(path: str) -> bool:
    lower = path.casefold()
    return (
        lower.endswith("/etc/db_schema_whitelist.json")
        or lower.endswith((
            ".php", ".inc", ".xml", ".graphqls", ".phtml",
            ".js", ".mjs", ".html",
        ))
        or path in {"bin/magento", "composer.json"}
    )


def policy_audit(repository: Path) -> dict[str, object]:
    rag_source = PROJECT_ROOT / "python-ecosystem" / "rag-pipeline" / "src"
    sys.path.insert(0, str(rag_source))
    from rag_pipeline.core.loader import DocumentLoader
    from rag_pipeline.models.config import RAGConfig

    loader = DocumentLoader(RAGConfig())
    files = tuple(loader.iter_repository_files(repository))
    relative_paths = tuple(path.as_posix() for path in files)
    catalog = PluginCatalog.discover(PROJECT_ROOT / "analysis-plugins")
    capabilities = ProjectSelector(catalog.registry).select(RepositoryFacts(
        revision="working-tree",
        paths=tuple(sorted(relative_paths)),
        marker_contents={
            "composer.json": (repository / "composer.json").read_text(encoding="utf-8")
        } if (repository / "composer.json").is_file() else {},
    ))
    runtime = PluginRuntime(catalog)
    disposition_by_path = {
        path: runtime.file_disposition(path, capabilities).value
        for path in relative_paths
    }
    dispositions = Counter(disposition_by_path.values())
    full_paths = tuple(
        path for path in relative_paths
        if runtime.file_disposition(path, capabilities).value == "full"
    )
    return {
        "status": "passed",
        "repository": str(repository),
        "plugins": list(capabilities.repository_plugins),
        "loaderFiles": len(files),
        "fileDispositions": dict(sorted(dispositions.items())),
        "semanticFiles": dispositions["full"],
        "architectureOnlyFiles": dispositions["architecture-only"],
        "excludedByPlugin": dispositions["excluded"],
        "fullByExtension": dict(sorted(Counter(
            Path(path).suffix.casefold() or "<none>" for path in full_paths
        ).items())),
        "fullByTopDirectory": dict(sorted(Counter(
            path.split("/", 1)[0] for path in full_paths
        ).items())),
        "fullVendorBySecondDirectory": dict(sorted(Counter(
            path.split("/", 2)[1]
            for path in full_paths
            if path.startswith("vendor/") and path.count("/") >= 2
        ).items())),
    }


def _paths(repository: Path, max_files: int) -> tuple[Path, ...]:
    selected = tuple(sorted(
        (
            path
            for path in repository.rglob("*")
            if path.is_file()
            and ".git" not in path.relative_to(repository).parts
            and _candidate(path.relative_to(repository).as_posix())
        ),
        key=lambda path: path.relative_to(repository).as_posix(),
    ))
    if len(selected) > max_files:
        raise RuntimeError(
            f"selected file count {len(selected)} exceeds audit limit {max_files}"
        )
    return selected


def audit(repository: Path, max_files: int, max_file_bytes: int) -> dict[str, object]:
    started = time.monotonic()
    files = _paths(repository, max_files)
    relative_paths = tuple(path.relative_to(repository).as_posix() for path in files)
    marker_contents = {}
    composer = repository / "composer.json"
    if composer.is_file():
        marker_contents["composer.json"] = composer.read_text(encoding="utf-8")

    catalog = PluginCatalog.discover(PROJECT_ROOT / "analysis-plugins")
    capabilities = ProjectSelector(catalog.registry).select(RepositoryFacts(
        revision="working-tree",
        paths=relative_paths,
        marker_contents=marker_contents,
    ))
    if "magento" not in capabilities.repository_plugins:
        raise RuntimeError("Magento plugin was not selected for the repository")

    runtime = PluginRuntime(catalog)
    disposition_by_path = {
        path: runtime.file_disposition(path, capabilities).value
        for path in relative_paths
    }
    dispositions = Counter(disposition_by_path.values())
    handle = runtime.start_repository_analysis(
        capabilities,
        "working-tree",
    )
    ingested = 0
    skipped_large = []
    skipped_decode = []
    batch = []
    for path, relative in zip(files, relative_paths):
        if disposition_by_path[relative] == "excluded":
            continue
        size = path.stat().st_size
        if size > max_file_bytes:
            skipped_large.append(relative)
            continue
        try:
            content = path.read_text(encoding="utf-8")
        except UnicodeDecodeError:
            skipped_decode.append(relative)
            continue
        batch.append(FileArtifact(relative, content))
        ingested += 1
        if len(batch) == 100:
            handle.ingest(tuple(batch))
            batch.clear()
    if batch:
        handle.ingest(tuple(batch))

    logging.info(
        "Audit ingestion complete: files=%s elapsed=%.3fs",
        ingested,
        time.monotonic() - started,
    )
    finish_started = time.monotonic()
    analysis, diagnostics = handle.finish()
    logging.info(
        "Audit repository finish complete: elapsed=%.3fs",
        time.monotonic() - finish_started,
    )
    packet_kinds = Counter(packet.kind for packet in analysis.packets)
    fact_kinds = Counter(
        fact.kind
        for packet in analysis.packets
        for fact in packet.facts
    )
    architecture_groups = Counter(
        (packet.plugin_id, packet.kind, fact.path)
        for packet in analysis.packets
        for fact in packet.facts
    )
    architecture_points = sum(
        (fact_count + 24) // 25
        for fact_count in architecture_groups.values()
    )
    context_points = sum(
        (len(context.content) + 49_999) // 50_000
        for context in analysis.contexts
    )
    snapshot_points = sum(
        (len(snapshot.content) + 399_999) // 400_000
        for snapshot in analysis.snapshots
    )
    return {
        "repository": str(repository),
        "plugins": list(capabilities.repository_plugins),
        "filesSelected": len(files),
        "filesIngested": ingested,
        "candidateFileDispositions": dict(sorted(dispositions.items())),
        "skippedLarge": skipped_large,
        "skippedDecode": skipped_decode,
        "symbols": len(analysis.symbols),
        "packets": len(analysis.packets),
        "facts": sum(fact_kinds.values()),
        "storage": {
            "architectureGroups": len(architecture_groups),
            "architecturePoints": architecture_points,
            "exactSourcePoints": context_points,
            "snapshotPoints": snapshot_points,
            "totalZeroVectorPoints": (
                architecture_points + context_points + snapshot_points
            ),
        },
        "packetKinds": dict(sorted(packet_kinds.items())),
        "factKinds": dict(sorted(fact_kinds.items())),
        "diagnostics": [
            {
                "plugin": diagnostic.plugin_id,
                "code": diagnostic.code,
                "message": diagnostic.message,
            }
            for diagnostic in diagnostics
        ],
        "snapshots": [
            {
                "plugin": snapshot.plugin_id,
                "kind": snapshot.kind,
                "encodedBytes": len(snapshot.content),
            }
            for snapshot in analysis.snapshots
        ],
        "elapsedSeconds": round(time.monotonic() - started, 3),
    }


def main() -> int:
    logging.basicConfig(level=logging.INFO, format="%(levelname)s %(message)s")
    parser = argparse.ArgumentParser()
    parser.add_argument("repository", type=Path)
    parser.add_argument("--max-files", type=int, default=150_000)
    parser.add_argument("--max-file-bytes", type=int, default=1_048_576)
    parser.add_argument("--policy-only", action="store_true")
    arguments = parser.parse_args()
    repository = arguments.repository.resolve()
    if not repository.is_dir():
        parser.error(f"repository does not exist: {repository}")

    try:
        report = (
            policy_audit(repository)
            if arguments.policy_only
            else audit(repository, arguments.max_files, arguments.max_file_bytes)
        )
    except Exception as exception:
        print(json.dumps({
            "status": "failed",
            "error": f"{type(exception).__name__}: {exception}",
        }, indent=2, sort_keys=True))
        return 1
    if arguments.policy_only:
        print(json.dumps(report, indent=2, sort_keys=True))
        return 0
    report["status"] = "passed" if not report["diagnostics"] else "failed"
    print(json.dumps(report, indent=2, sort_keys=True))
    return 0 if report["status"] == "passed" and report["packets"] else 1


if __name__ == "__main__":
    raise SystemExit(main())
