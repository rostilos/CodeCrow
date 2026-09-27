"""QA evidence records, provenance, and deterministic stage-result merging."""
import json
import re
from dataclasses import dataclass
from typing import Any, Dict, List, Sequence


@dataclass(frozen=True)
class QaSemanticRecord:
    """One QA evidence record or bounded free-text continuation."""

    key: str
    section: str
    text: str
    paths: tuple[str, ...] = ()
    source_key: str = ""
    part_index: int = 1
    part_count: int = 1
    character_start: int = 0
    character_end: int = 0
    source_character_count: int = 0

    @property
    def identity(self) -> str:
        return self.source_key or self.key

    @property
    def display_key(self) -> str:
        if self.part_count <= 1:
            return self.key
        return (
            f"{self.identity}:part:{self.part_index:06d}-of-"
            f"{self.part_count:06d}"
        )

    def envelope(self) -> str:
        """Render provenance without changing or clipping the owned text."""
        metadata = {
            "recordKey": self.identity,
            "section": self.section,
            "paths": list(self.paths),
            "partIndex": self.part_index,
            "partCount": self.part_count,
            "characterStart": self.character_start,
            "characterEnd": self.character_end or len(self.text),
            "sourceCharacterCount": self.source_character_count or len(self.text),
        }
        return (
            "[QA_SEMANTIC_RECORD "
            + json.dumps(
                metadata,
                ensure_ascii=False,
                sort_keys=True,
                separators=(",", ":"),
            )
            + "]\n"
            + self.text
        )


QA_SEMANTIC_SHARD_NOTICE = (
    "BOUNDED QA SEMANTIC SHARD: this request owns an admitted subset of input "
    "records. A QA_COVERAGE_DIAGNOSTIC record identifies partial coverage when "
    "the finite invocation ceiling omitted evidence. Do not "
    "interpret local absence as evidence that a change or requirement does "
    "not exist."
)

def record_text(
    records: Sequence[QaSemanticRecord],
    *sections: str,
    empty: str,
) -> str:
    selected = [
        record.envelope()
        for record in records
        if record.section in sections
    ]
    if not selected:
        return empty
    return QA_SEMANTIC_SHARD_NOTICE + "\n\n" + "\n\n".join(selected)


def record_paths(records: Sequence[QaSemanticRecord]) -> List[str]:
    return sorted({path for record in records for path in record.paths if path})


def text_record(
    key: str,
    section: str,
    text: Any,
    *,
    paths: Sequence[str] = (),
) -> QaSemanticRecord:
    normalized = text if isinstance(text, str) else str(text)
    return QaSemanticRecord(
        key=key,
        section=section,
        text=normalized,
        paths=tuple(paths),
        character_end=len(normalized),
        source_character_count=len(normalized),
    )


def json_leaf_records(
    value: Any,
    *,
    key_prefix: str,
    section: str,
    path: tuple[Any, ...] = (),
) -> List[QaSemanticRecord]:
    """Represent every JSON leaf/empty container as a complete path record."""
    if isinstance(value, dict) and value:
        return [
            record
            for key in sorted(value, key=str)
            for record in json_leaf_records(
                value[key],
                key_prefix=key_prefix,
                section=section,
                path=(*path, key),
            )
        ]
    if isinstance(value, (list, tuple)) and value:
        return [
            record
            for index, item in enumerate(value)
            for record in json_leaf_records(
                item,
                key_prefix=key_prefix,
                section=section,
                path=(*path, index),
            )
        ]

    pointer = "/" + "/".join(
        str(item).replace("~", "~0").replace("/", "~1")
        for item in path
    )
    # String leaves are free text: keep their exact bytes directly under
    # the JSON-pointer-bearing record key so line/whitespace packing can
    # split them semantically. Other scalar/empty values remain explicit
    # typed JSON records.
    payload = (
        value
        if isinstance(value, str)
        else json.dumps(
            {"jsonPointer": pointer or "/", "value": value},
            ensure_ascii=False,
            sort_keys=True,
            separators=(",", ":"),
            default=str,
        )
    )
    suffix = pointer or "/"
    return [text_record(
        f"{key_prefix}:{suffix}",
        section,
        payload,
    )]


def diff_records(
    diff: str,
    *,
    key_prefix: str,
    section: str = "diff",
) -> List[QaSemanticRecord]:
    if not diff:
        return []
    sections = re.split(r"(?=^diff --git )", diff, flags=re.MULTILINE)
    records: List[QaSemanticRecord] = []
    for index, chunk in enumerate(sections, start=1):
        if not chunk:
            continue
        match = re.match(r"diff --git a/(.+?) b/(.+?)(?:\n|$)", chunk)
        paths = tuple(sorted(set(match.groups()))) if match else ()
        records.append(text_record(
            f"{key_prefix}:{index:06d}",
            section,
            chunk,
            paths=paths,
        ))
    return records


def shared_records(
    placeholders: Dict[str, str],
    fields: Sequence[str],
) -> tuple[Dict[str, str], List[QaSemanticRecord]]:
    detached = dict(placeholders)
    records: List[QaSemanticRecord] = []
    for field in fields:
        value = detached.get(field)
        if not isinstance(value, str) or not value:
            continue
        records.append(text_record(
            f"shared:{field}",
            f"shared:{field}",
            value,
        ))
        detached[field] = (
            f"[Complete {field} is assigned once in QA semantic records.]"
        )
    return detached, records


def stable_union(values: Sequence[Any]) -> List[Any]:
    result: List[Any] = []
    seen: set[str] = set()
    for value in values:
        identity = json.dumps(
            value,
            ensure_ascii=False,
            sort_keys=True,
            separators=(",", ":"),
            default=str,
        )
        if identity in seen:
            continue
        seen.add(identity)
        result.append(value)
    return result


def merge_stage_2_results(
    results: Sequence[Dict[str, Any]],
) -> Dict[str, Any]:
    list_fields = (
        "cross_file_scenarios",
        "cascading_risks",
        "uncovered_acceptance_criteria",
    )
    merged: Dict[str, Any] = {
        field: stable_union([
            item
            for result in results
            for item in (result.get(field) or [])
        ])
        for field in list_fields
    }
    diagnostics = [
        str(result.get("error"))
        for result in results
        if result.get("error")
    ]
    if diagnostics:
        merged["partial_errors"] = stable_union(diagnostics)
    raw = [
        str(result.get("raw_analysis"))
        for result in results
        if result.get("raw_analysis")
    ]
    if raw:
        merged["raw_analysis"] = "\n\n".join(raw)
    return merged
