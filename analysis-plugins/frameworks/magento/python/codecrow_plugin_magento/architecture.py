from __future__ import annotations

import re
import xml.etree.ElementTree as ET
from dataclasses import dataclass, field
from dataclasses import replace

from codecrow_plugins import ArchitecturePacket, GraphFact, PluginDiagnostic


MAGENTO_AREAS = (
    "adminhtml",
    "crontab",
    "frontend",
    "graphql",
    "webapi_rest",
    "webapi_soap",
)


def is_magento_config_xml(path: str) -> bool:
    """Return whether an XML path participates in Magento config merging.

    A module configuration file is either directly below ``etc`` or below one
    known Magento area. Deeper/custom directories such as ``etc/samples`` are
    ordinary project content, even though their path contains an ``etc``
    segment. Treating every descendant as Magento configuration makes valid
    payload formats such as cXML part of repository architecture analysis.
    """
    normalized = path.replace("\\", "/").strip("/").casefold()
    if not normalized.endswith(".xml"):
        return False
    marker = "/etc/"
    candidate = f"/{normalized}"
    if marker not in candidate:
        return False
    tail = candidate.split(marker, 1)[1].split("/")
    return (
        len(tail) == 1
        or (len(tail) == 2 and tail[0] in MAGENTO_AREAS)
    )


def tag(element: ET.Element) -> str:
    return element.tag.rsplit("}", 1)[-1]


def line(content: str, needle: str) -> int:
    offset = content.find(needle)
    return content.count("\n", 0, max(offset, 0)) + 1


def safe_xml(plugin_id: str, path: str, content: str):
    lowered = content.casefold()
    if "<!doctype" in lowered or "<!entity" in lowered:
        return None, PluginDiagnostic(
            "magento-unsafe-xml",
            "DTD/entity declarations are forbidden in Magento "
            f"configuration {path}",
            plugin_id,
            path=path,
            recoverable=True,
        )
    try:
        return ET.fromstring(content), None
    except ET.ParseError as exception:
        return None, PluginDiagnostic(
            "magento-invalid-xml",
            f"Cannot parse {path}: {exception}",
            plugin_id,
            path=path,
            recoverable=True,
        )


def config_area(path: str, filename: str) -> str | None:
    suffix = f"/etc/{filename}"
    if path == f"app/etc/{filename}":
        return "initial"
    if path.endswith(suffix):
        return "global"
    match = re.search(rf"/etc/([^/]+)/{re.escape(filename)}$", f"/{path}")
    return match.group(1) if match else None


def view_area(path: str, directory: str) -> str | None:
    match = re.search(rf"/view/([^/]+)/{re.escape(directory)}/", f"/{path}")
    if match:
        return match.group(1)
    theme_match = re.match(
        rf"app/design/(frontend|adminhtml)/[^/]+/[^/]+/(?:[^/]+/)?{re.escape(directory)}/",
        path,
    )
    return theme_match.group(1) if theme_match else None


def attrs(**values: object) -> tuple[tuple[str, str], ...]:
    return tuple(sorted(
        (key, str(value))
        for key, value in values.items()
        if value is not None and str(value) != ""
    ))


@dataclass(frozen=True)
class ModuleRecord:
    name: str
    root: str
    module_xml: str
    sequence: tuple[str, ...]
    enabled: bool
    order: int


@dataclass
class PacketAccumulator:
    plugin_id: str
    kind: str
    key: str
    paths: set[str] = field(default_factory=set)
    facts: set[GraphFact] = field(default_factory=set)
    attributes: dict[str, str] = field(default_factory=dict)

    def add(self, fact: GraphFact, *paths: str) -> None:
        related_paths = tuple(sorted({*fact.related_paths, *(path for path in paths if path)}))
        if related_paths != fact.related_paths:
            fact = replace(fact, related_paths=related_paths)
        self.facts.add(fact)
        self.paths.add(fact.path)
        self.paths.update(related_paths)

    def build(self) -> ArchitecturePacket:
        return ArchitecturePacket(
            plugin_id=self.plugin_id,
            kind=self.kind,
            key=self.key,
            paths=tuple(sorted(self.paths)),
            facts=tuple(sorted(self.facts)),
            attributes=tuple(sorted(self.attributes.items())),
        )


class PacketGraph:
    def __init__(self, plugin_id: str) -> None:
        self.plugin_id = plugin_id
        self._packets: dict[tuple[str, str], PacketAccumulator] = {}

    def packet(self, kind: str, key: str, **attributes: str) -> PacketAccumulator:
        identity = (kind, key)
        packet = self._packets.setdefault(
            identity,
            PacketAccumulator(self.plugin_id, kind, key),
        )
        packet.attributes.update({key: value for key, value in attributes.items() if value})
        return packet

    def build(self) -> tuple[ArchitecturePacket, ...]:
        return tuple(sorted(
            packet.build()
            for packet in self._packets.values()
            if packet.paths and packet.facts
        ))
