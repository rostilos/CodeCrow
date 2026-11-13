from __future__ import annotations

import re
from dataclasses import dataclass, field


_MODULE_ENABLED = re.compile(
    r"['\"](?P<name>[A-Za-z][A-Za-z0-9]*_[A-Za-z][A-Za-z0-9]*)['\"]\s*=>\s*(?P<enabled>[01])"
)

_MODULES_SECTION = re.compile(
    r"['\"]modules['\"]\s*=>\s*\[(?P<body>.*?)\]",
    re.DOTALL,
)

_REGISTRATION = re.compile(
    r"ComponentRegistrar::MODULE\s*,\s*['\"](?P<name>[A-Za-z0-9_]+)['\"]"
)

_THEME_REGISTRATION = re.compile(
    r"ComponentRegistrar::THEME\s*,\s*['\"](?P<name>[^'\"]+)['\"]"
)

_PHP_TEMPLATE_CALL_REFERENCE = "php-template-instance-call-reference:"

_DEPLOYMENT_DEFAULT_CONNECTION = "deployment-default"

_BROKER_DEFAULT_EXCHANGE = "broker-default-exchange"

_DEFAULT_MESSAGE_CONSUMER = (
    r"Magento\Framework\MessageQueue\Consumer"
)

_BUILTIN_MESSAGE_CONSUMERS = frozenset({
    _DEFAULT_MESSAGE_CONSUMER,
    r"Magento\Framework\MessageQueue\BatchConsumer",
})

_MASS_MESSAGE_CONSUMER = (
    r"Magento\AsynchronousOperations\Model\MassConsumer"
)

def _enabled(value: object, default: bool = True) -> bool:
    if value is None or str(value).strip() == "":
        return default
    return str(value).strip().casefold() not in {"1", "true", "yes", "on"}

def _message_topic_matches(pattern: str, topic: str) -> bool:
    """Mirror Magento QueueResolver's AMQP `*`/`#` topic matching."""
    if pattern == topic:
        return True
    if "*" not in pattern and "#" not in pattern:
        return False

    pattern_parts = pattern.split(".")
    topic_parts = topic.split(".")
    pattern_index = 0
    topic_index = 0
    hash_pattern_index = -1
    hash_topic_index = -1

    while topic_index < len(topic_parts):
        part = (
            pattern_parts[pattern_index]
            if pattern_index < len(pattern_parts)
            else None
        )
        if part == "#":
            hash_pattern_index = pattern_index
            hash_topic_index = topic_index
            pattern_index += 1
            continue
        if part is not None and (
            part == "*" or part == topic_parts[topic_index]
        ):
            pattern_index += 1
            topic_index += 1
            continue
        if hash_pattern_index == -1:
            return False
        hash_topic_index += 1
        topic_index = hash_topic_index
        pattern_index = hash_pattern_index + 1

    while (
        pattern_index < len(pattern_parts)
        and pattern_parts[pattern_index] == "#"
    ):
        pattern_index += 1
    return pattern_index == len(pattern_parts)

@dataclass(frozen=True)
class ConfigValue:
    value: str
    path: str
    line: int
    module: str
    order: int
    attributes: tuple[tuple[str, str], ...] = ()
    position: int = 0

@dataclass
class DiState:
    preferences: dict[str, ConfigValue] = field(default_factory=dict)
    virtual_types: dict[str, ConfigValue] = field(default_factory=dict)
    plugins: dict[tuple[str, str], ConfigValue] = field(default_factory=dict)
    arguments: dict[tuple[str, str, str], ConfigValue] = field(default_factory=dict)
    argument_types: dict[tuple[str, str], ConfigValue] = field(default_factory=dict)
    item_types: dict[tuple[str, str, str], ConfigValue] = field(default_factory=dict)
    item_values: dict[tuple[str, str, str], ConfigValue] = field(default_factory=dict)

@dataclass(frozen=True)
class ThemeRecord:
    name: str
    area: str
    root: str
    theme_xml: str
    parent: str = ""

@dataclass(frozen=True, order=True)
class TemplatePhpCall:
    receiver: str
    method: str
    line: int
    literal_arguments: tuple[tuple[int, str], ...] = ()

def _module_root(path: str) -> str:
    suffix = "/etc/module.xml"
    if path == "etc/module.xml":
        return ""
    return path[:-len(suffix)] if path.endswith(suffix) else ""

def _path_under(root: str, relative: str) -> str:
    return f"{root}/{relative}" if root else relative

def _method_subject(method: str) -> tuple[str, str] | None:
    for prefix in ("before", "around", "after"):
        if method.startswith(prefix) and len(method) > len(prefix):
            subject = method[len(prefix):]
            return prefix, subject[:1].casefold() + subject[1:]
    return None

@dataclass
class ConfigurationSources:
    """Evidence emitted by configuration/routes and consumed by dependent stages."""
    acl: dict[str, set[str]] = field(default_factory=dict)
    controllers: dict[str, set[str]] = field(default_factory=dict)
    system: dict[str, set[str]] = field(default_factory=dict)


@dataclass
class LayoutSources:
    """Layout evidence consumed by frontend source resolution."""
    templates: dict[str, set[tuple[str, str, str]]] = field(default_factory=dict)
    conditional_templates: dict[str, set[tuple[str, str, str]]] = field(default_factory=dict)
