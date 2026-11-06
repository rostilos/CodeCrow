from __future__ import annotations

import json
import re
from dataclasses import dataclass
from html.parser import HTMLParser

from .javascript import OptionalJavaScriptEnrichmentError


class MalformedFrontendInitSource(OptionalJavaScriptEnrichmentError):
    """A literal Magento frontend initializer is not valid JSON."""

    diagnostic_code = "magento-frontend-init-source-malformed"
    source_specific = True


@dataclass(frozen=True, order=True)
class FrontendInitReference:
    source_kind: str
    selector: str
    component: str
    line: int
    position: int = 0


_PHP = re.compile(r"<\?(?:php|=)?.*?\?>", re.IGNORECASE | re.DOTALL)
_MAGENTO_INIT_TYPES = frozenset({
    "application/x-magento-init",
    "text/x-magento-init",
    "x-magento-init",
})


def _mask_php(content: str) -> str:
    return _PHP.sub(
        lambda match: "".join(
            "\n" if character == "\n" else " "
            for character in match.group(0)
        ),
        content,
    )


def _literal_component(value: object) -> str:
    if not isinstance(value, str):
        return ""
    candidate = value.strip()
    if (
        not candidate
        or candidate == "*"
        or len(candidate) > 512
        or "<?" in candidate
        or any(character.isspace() for character in candidate)
    ):
        return ""
    return candidate


def _json_object(content: str) -> dict[str, object] | None:
    masked = _mask_php(content)
    try:
        document = json.loads(masked)
    except json.JSONDecodeError as exception:
        # Server-computed structure is intentionally unresolved. A static
        # malformed literal is observable optional-enrichment degradation.
        if "<?" in content:
            return None
        raise MalformedFrontendInitSource(
            "literal Magento frontend initializer contains invalid JSON"
        ) from exception
    return document if isinstance(document, dict) else None


def _component_line(content: str, component: str, base_line: int) -> int:
    offset = content.find(f'"{component}"')
    return base_line + content.count("\n", 0, max(offset, 0))


class _FrontendInitParser(HTMLParser):
    def __init__(self) -> None:
        super().__init__(convert_charrefs=True)
        self.references: set[FrontendInitReference] = set()
        self._script_type = ""
        self._script_line = 1
        self._script_chunks: list[str] = []

    def handle_starttag(
        self,
        tag: str,
        attributes: list[tuple[str, str | None]],
    ) -> None:
        normalized = {
            name.casefold(): value
            for name, value in attributes
            if value is not None
        }
        mage_init = normalized.get("data-mage-init")
        if mage_init is not None:
            document = _json_object(mage_init)
            if document is not None:
                for position, component in enumerate(document):
                    literal = _literal_component(component)
                    if literal:
                        self.references.add(FrontendInitReference(
                            "data-mage-init",
                            "self",
                            literal,
                            self.getpos()[0],
                            position,
                        ))

        script_type = normalized.get("type", "").strip().casefold()
        if tag.casefold() == "script" and script_type in _MAGENTO_INIT_TYPES:
            self._script_type = script_type
            self._script_line = self.getpos()[0]
            self._script_chunks = []

    def handle_startendtag(
        self,
        tag: str,
        attributes: list[tuple[str, str | None]],
    ) -> None:
        self.handle_starttag(tag, attributes)
        self.handle_endtag(tag)

    def handle_data(self, data: str) -> None:
        if self._script_type:
            self._script_chunks.append(data)

    def handle_endtag(self, tag: str) -> None:
        if tag.casefold() != "script" or not self._script_type:
            return
        content = "".join(self._script_chunks)
        document = _json_object(content)
        if document is not None:
            for selector, initializers in document.items():
                if not isinstance(selector, str) or not isinstance(
                    initializers,
                    dict,
                ):
                    continue
                for position, component in enumerate(initializers):
                    literal = _literal_component(component)
                    if literal:
                        self.references.add(FrontendInitReference(
                            "x-magento-init",
                            selector,
                            literal,
                            _component_line(
                                content,
                                literal,
                                self._script_line,
                            ),
                            position,
                        ))
        self._script_type = ""
        self._script_chunks = []

    def close(self) -> None:
        super().close()
        if self._script_type:
            raise MalformedFrontendInitSource(
                "literal Magento frontend initializer has no closing script tag"
            )


def extract_frontend_initializers(
    content: str,
) -> tuple[FrontendInitReference, ...]:
    """Extract exact literal Magento initializers from a template source."""

    parser = _FrontendInitParser()
    parser.feed(content)
    parser.close()
    return tuple(sorted(parser.references))
