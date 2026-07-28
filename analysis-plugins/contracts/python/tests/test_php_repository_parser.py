from __future__ import annotations

import json
from pathlib import Path

import pytest

from codecrow_plugins import FileArtifact, OutcomeStatus, PluginCatalog, RepositoryAnalysis


pytest.importorskip("tree_sitter")
pytest.importorskip("tree_sitter_php")

PLUGINS_ROOT = Path(__file__).resolve().parents[3]


def test_php_repository_parser_resolves_namespaces_imports_and_constructor_types():
    catalog = PluginCatalog.discover(PLUGINS_ROOT)
    plugin = catalog.implementation("php")
    started = plugin.start_repository_analysis("0123456789abcdef")
    assert started.status is OutcomeStatus.HANDLED
    started.value.ingest((FileArtifact(
        "app/code/Acme/Checkout/Model/Service.php",
        r"""<?php
namespace Acme\Checkout\Model;

use Acme\Checkout\Api\CartInterface;
use Acme\Shared\BaseService;
use Acme\Shared\OptionalDependency as OptionalAlias;
use Psr\Log\LoggerInterface;

class Service extends BaseService implements CartInterface
{
    public function __construct(
        CartInterface $cart,
        LoggerInterface $logger,
        ?OptionalAlias $optional = null
    ) {}

    public function execute(): void {}
}
""",
    ),))

    outcome = started.value.finish(RepositoryAnalysis())

    assert outcome.status is OutcomeStatus.HANDLED
    symbol = outcome.value.symbols[0]
    assert symbol.qualified_name == "Acme\\Checkout\\Model\\Service"
    assert symbol.parents == (
        "Acme\\Checkout\\Api\\CartInterface",
        "Acme\\Shared\\BaseService",
    )
    assert {
        key: value
        for key, value in symbol.attributes
        if key.startswith(("php-parent-", "php-interface:"))
    } == {
        "php-interface:0000": "Acme\\Checkout\\Api\\CartInterface",
        "php-parent-class": "Acme\\Shared\\BaseService",
    }
    assert symbol.methods == ("__construct", "execute")
    assert symbol.constructor_types == (
        "Acme\\Checkout\\Api\\CartInterface",
        "Acme\\Shared\\OptionalDependency",
        "Psr\\Log\\LoggerInterface",
    )
    assert dict(symbol.attributes)["method:execute:returnType"] == "void"


def test_php_repository_parser_preserves_exact_declared_return_types():
    catalog = PluginCatalog.discover(PLUGINS_ROOT)
    plugin = catalog.implementation("php")
    started = plugin.start_repository_analysis("php-return-types")
    started.value.ingest((FileArtifact(
        "app/code/Acme/Model/Repository.php",
        r"""<?php
namespace Acme\Model;

use Acme\Api\ResultInterface as Result;

class Repository
{
    public function find(): ?Result {}
    public function load(): Result|array|null {}
    public function dynamic() {}
}
""",
    ),))

    analysis = started.value.finish(RepositoryAnalysis()).value
    symbol = analysis.symbols[0]
    attributes = dict(symbol.attributes)

    assert attributes["method:find:returnType"] == (
        "?Acme\\Api\\ResultInterface"
    )
    assert attributes["method:load:returnType"] == (
        "Acme\\Api\\ResultInterface|array|null"
    )
    assert "method:dynamic:returnType" not in attributes

    restored = plugin.restore_repository_analysis(
        "php-return-types-restored",
        analysis.snapshots,
    )
    replay = restored.value.finish(RepositoryAnalysis()).value
    assert replay.symbols == analysis.symbols


def test_php_repository_parser_preserves_exact_literal_constant_topology():
    catalog = PluginCatalog.discover(PLUGINS_ROOT)
    plugin = catalog.implementation("php")
    started = plugin.start_repository_analysis("php-constant-topology")
    started.value.ingest((
        FileArtifact(
            "app/code/Perspective/Prices/Pricing/Price/WeightPrice.php",
            r"""<?php
namespace Perspective\Prices\Pricing\Price;

class WeightPrice
{
    public const PRICE_CODE = 'price_per_specific_weight';
    public const DYNAMIC = SOME_OTHER_CONSTANT;
}
""",
        ),
        FileArtifact(
            "app/code/Perspective/SeoMarkup/Converter.php",
            r"""<?php
namespace Perspective\SeoMarkup;

use Perspective\Prices\Pricing\Price\WeightPrice as RegisteredPrice;

class Converter
{
    public function convert($product)
    {
        $value = $product->getPriceInfo()
            ->getPrice(RegisteredPrice::PRICE_CODE)
            ->getValue();
        $unrelated = RegisteredPrice::PRICE_CODE;
        return $value;
    }
}
""",
        ),
    ))

    symbols = {
        symbol.qualified_name: symbol
        for symbol in started.value.finish(RepositoryAnalysis()).value.symbols
    }
    provider_attributes = [
        json.loads(value)
        for key, value in symbols[
            "Perspective\\Prices\\Pricing\\Price\\WeightPrice"
        ].attributes
        if key.startswith("php-class-constant:")
    ]
    assert provider_attributes == [{
        "line": 6,
        "name": "PRICE_CODE",
        "value": "price_per_specific_weight",
    }]

    references = [
        json.loads(value)
        for key, value in symbols[
            "Perspective\\SeoMarkup\\Converter"
        ].attributes
        if key.startswith("php-class-constant-reference:")
    ]
    assert references == [
        {
            "argumentOf": "getPrice",
            "constant": "PRICE_CODE",
            "line": 11,
            "target": (
                "Perspective\\Prices\\Pricing\\Price\\WeightPrice"
            ),
        },
        {
            "constant": "PRICE_CODE",
            "line": 13,
            "target": (
                "Perspective\\Prices\\Pricing\\Price\\WeightPrice"
            ),
        },
    ]


def test_php_repository_parser_preserves_bounded_literal_instance_call_arguments():
    catalog = PluginCatalog.discover(PLUGINS_ROOT)
    plugin = catalog.implementation("php")
    started = plugin.start_repository_analysis(
        "php-literal-instance-call-topology"
    )
    started.value.ingest((FileArtifact(
        "app/code/Acme/Checkout/Model/ConfigReader.php",
        r"""<?php
namespace Acme\Checkout\Model;

use Magento\Framework\App\Config\ScopeConfigInterface;

class ConfigReader
{
    public function __construct(
        private ScopeConfigInterface $scopeConfig
    ) {}

    public function mode(string $dynamicPath): string
    {
        $mode = $this->scopeConfig->getValue(
            'acme/cart/runtime_mode'
        );
        $enabled = $this->scopeConfig->isSetFlag(
            "acme/cart/enabled"
        );
        $dynamic = $this->scopeConfig->getValue($dynamicPath);
        $interpolated = $this->scopeConfig->getValue(
            "acme/cart/{$dynamicPath}"
        );
        return $enabled ? $mode : $dynamic . $interpolated;
    }
}
""",
    ),))

    analysis = started.value.finish(RepositoryAnalysis()).value
    symbol = analysis.symbols[0]
    references = [
        json.loads(value)
        for key, value in symbol.attributes
        if key.startswith("php-literal-instance-call-reference:")
    ]

    assert references == [
        {
            "caller": "mode",
            "line": 14,
            "literalStringArguments": {
                "0": "acme/cart/runtime_mode",
            },
            "method": "getValue",
            "receiverResolution": "declared-property",
            "target": (
                "Magento\\Framework\\App\\Config\\"
                "ScopeConfigInterface"
            ),
        },
        {
            "caller": "mode",
            "line": 17,
            "literalStringArguments": {
                "0": "acme/cart/enabled",
            },
            "method": "isSetFlag",
            "receiverResolution": "declared-property",
            "target": (
                "Magento\\Framework\\App\\Config\\"
                "ScopeConfigInterface"
            ),
        },
    ]


def test_php_repository_parser_resolves_only_unconditional_unique_local_literals():
    catalog = PluginCatalog.discover(PLUGINS_ROOT)
    plugin = catalog.implementation("php")
    started = plugin.start_repository_analysis(
        "php-local-literal-instance-call-topology"
    )
    started.value.ingest((FileArtifact(
        "app/code/Acme/Email/Model/Sender.php",
        r"""<?php
namespace Acme\Email\Model;

use Magento\Framework\Mail\Template\TransportBuilder;

class Sender
{
    public function __construct(
        private TransportBuilder $transportBuilder
    ) {}

    public function send(
        string $dynamic,
        bool $flag
    ): void {
        $templateId = 'acme_order';
        $this->transportBuilder->setTemplateIdentifier($templateId);
        $this->transportBuilder->setTemplateIdentifier($dynamic);

        if ($flag) {
            $conditional = 'conditional_order';
        }
        $this->transportBuilder->setTemplateIdentifier($conditional);

        $conflicting = 'first_order';
        $conflicting = 'second_order';
        $this->transportBuilder->setTemplateIdentifier($conflicting);
    }
}
""",
    ),))

    analysis = started.value.finish(RepositoryAnalysis()).value
    references = [
        json.loads(value)
        for key, value in analysis.symbols[0].attributes
        if key.startswith("php-literal-instance-call-reference:")
    ]
    assert references == [{
        "caller": "send",
        "line": 17,
        "literalArgumentResolution": {
            "0": "local-exact-assignment",
        },
        "literalStringArguments": {
            "0": "acme_order",
        },
        "method": "setTemplateIdentifier",
        "receiverResolution": "declared-property",
        "target": "Magento\\Framework\\Mail\\Template\\TransportBuilder",
    }]


def test_php_repository_snapshot_replaces_and_deletes_symbols_by_path():
    catalog = PluginCatalog.discover(PLUGINS_ROOT)
    plugin = catalog.implementation("php")
    path = "app/code/Acme/Checkout/Model/Service.php"
    started = plugin.start_repository_analysis("base")
    started.value.ingest((FileArtifact(
        path,
        "<?php namespace Acme\\Checkout\\Model; class Service {}",
    ),))
    base = started.value.finish(RepositoryAnalysis()).value

    restored = plugin.restore_repository_analysis("changed", base.snapshots)
    restored.value.ingest((FileArtifact(
        path,
        "<?php namespace Acme\\Checkout\\Model; class Replacement {}",
    ),))
    changed = restored.value.finish(RepositoryAnalysis()).value
    assert [symbol.qualified_name for symbol in changed.symbols] == [
        "Acme\\Checkout\\Model\\Replacement"
    ]

    deleted = plugin.restore_repository_analysis("deleted", changed.snapshots)
    deleted.value.ingest((FileArtifact(path, "", deleted=True),))
    after_delete = deleted.value.finish(RepositoryAnalysis()).value
    assert after_delete.symbols == ()


def test_php_repository_parser_preserves_interception_relevant_modifiers():
    catalog = PluginCatalog.discover(PLUGINS_ROOT)
    plugin = catalog.implementation("php")
    started = plugin.start_repository_analysis("base")
    started.value.ingest((FileArtifact(
        "app/code/Acme/Checkout/Model/FinalService.php",
        """<?php
namespace Acme\\Checkout\\Model;

final class FinalService
{
    final protected static function guarded(): void {}
    function defaultPublic(): void {}
}
""",
    ),))

    base = started.value.finish(RepositoryAnalysis()).value
    symbol = base.symbols[0]
    assert dict(symbol.attributes) == {
        "method:defaultPublic:returnType": "void",
        "method:defaultPublic:visibility": "public",
        "method:guarded:final": "true",
        "method:guarded:returnType": "void",
        "method:guarded:static": "true",
        "method:guarded:visibility": "protected",
        "type:final": "true",
    }

    restored = plugin.restore_repository_analysis("overlay", base.snapshots)
    round_tripped = restored.value.finish(RepositoryAnalysis()).value.symbols[0]
    assert round_tripped.attributes == symbol.attributes


def test_php_repository_parser_preserves_runtime_parent_relation_order():
    catalog = PluginCatalog.discover(PLUGINS_ROOT)
    plugin = catalog.implementation("php")
    started = plugin.start_repository_analysis("parent-order")
    started.value.ingest((FileArtifact(
        "app/code/Acme/Model/Relations.php",
        r"""<?php
namespace Acme\Model;

interface RootInterface {}
interface LastInterface extends RootInterface {}
interface FirstInterface {}
class ParentService {}
class Service extends ParentService implements LastInterface, FirstInterface {}
""",
    ),))

    outcome = started.value.finish(RepositoryAnalysis())

    assert outcome.status is OutcomeStatus.HANDLED
    symbols = {
        symbol.qualified_name: symbol
        for symbol in outcome.value.symbols
    }
    assert dict(symbols["Acme\\Model\\LastInterface"].attributes) == {
        "php-parent-interface:0000": "Acme\\Model\\RootInterface",
    }
    assert {
        key: value
        for key, value in symbols["Acme\\Model\\Service"].attributes
        if key.startswith(("php-parent-", "php-interface:"))
    } == {
        "php-interface:0000": "Acme\\Model\\LastInterface",
        "php-interface:0001": "Acme\\Model\\FirstInterface",
        "php-parent-class": "Acme\\Model\\ParentService",
    }


def test_php_repository_emits_exact_internal_code_relations_and_trait_paths():
    catalog = PluginCatalog.discover(PLUGINS_ROOT)
    plugin = catalog.implementation("php")
    started = plugin.start_repository_analysis("php-relations")
    started.value.ingest((
        FileArtifact(
            "app/code/Acme/Api/Contract.php",
            "<?php namespace Acme\\Api; interface Contract {}",
        ),
        FileArtifact(
            "app/code/Acme/Model/AuditTrait.php",
            "<?php namespace Acme\\Model; trait AuditTrait {}",
        ),
        FileArtifact(
            "app/code/Acme/Model/BaseService.php",
            "<?php namespace Acme\\Model; class BaseService {}",
        ),
        FileArtifact(
            "app/code/Acme/Model/Service.php",
            r"""<?php
namespace Acme\Model;

use Acme\Api\Contract;
use External\Logger;

class Service extends BaseService implements Contract
{
    use AuditTrait;

    public function __construct(Contract $contract, Logger $logger) {}
}
""",
        ),
    ))

    analysis = started.value.finish(RepositoryAnalysis()).value

    service = next(
        symbol
        for symbol in analysis.symbols
        if symbol.qualified_name == "Acme\\Model\\Service"
    )
    assert {
        key: value
        for key, value in service.attributes
        if key.startswith("php-trait:")
    } == {"php-trait:0000": "Acme\\Model\\AuditTrait"}

    packet = next(
        packet
        for packet in analysis.packets
        if packet.key == "app/code/Acme/Model/Service.php"
    )
    assert packet.kind == "php-code-relation"
    assert packet.paths == (
        "app/code/Acme/Api/Contract.php",
        "app/code/Acme/Model/AuditTrait.php",
        "app/code/Acme/Model/BaseService.php",
        "app/code/Acme/Model/Service.php",
    )
    assert {
        (fact.kind, fact.relation, fact.target, fact.related_paths)
        for fact in packet.facts
    } == {
        (
            "php-constructor-dependency",
            "constructor-requires",
            "Acme\\Api\\Contract",
            ("app/code/Acme/Api/Contract.php",),
        ),
        (
            "php-inheritance",
            "extends",
            "Acme\\Model\\BaseService",
            ("app/code/Acme/Model/BaseService.php",),
        ),
        (
            "php-inheritance",
            "implements",
            "Acme\\Api\\Contract",
            ("app/code/Acme/Api/Contract.php",),
        ),
        (
            "php-trait-use",
            "uses-trait",
            "Acme\\Model\\AuditTrait",
            ("app/code/Acme/Model/AuditTrait.php",),
        ),
    }
    assert all(fact.target != "External\\Logger" for fact in packet.facts)

    restored = plugin.restore_repository_analysis(
        "php-relations-restored",
        analysis.snapshots,
    )
    replay = restored.value.finish(RepositoryAnalysis()).value
    assert replay.packets == analysis.packets


def test_php_repository_prioritizes_exact_trait_constructor_composition():
    catalog = PluginCatalog.discover(PLUGINS_ROOT)
    plugin = catalog.implementation("php")
    started = plugin.start_repository_analysis("php-trait-constructor")
    started.value.ingest((
        FileArtifact(
            "app/code/Acme/Model/Dependency.php",
            "<?php namespace Acme\\Model; class Dependency {}",
        ),
        FileArtifact(
            "app/code/Acme/Model/ProvidesService.php",
            r"""<?php
namespace Acme\Model;

trait ProvidesService
{
    public function __construct(
        protected readonly Dependency $dependency
    ) {}
}
""",
        ),
        FileArtifact(
            "app/code/Acme/Model/Consumer.php",
            r"""<?php
namespace Acme\Model;

class Consumer
{
    use ProvidesService;

    public function __construct(
        protected readonly Dependency $dependency
    ) {}
}
""",
        ),
        FileArtifact(
            "app/code/Acme/Model/InheritedConstructor.php",
            r"""<?php
namespace Acme\Model;

class InheritedConstructor
{
    use ProvidesService;
}
""",
        ),
    ))

    analysis = started.value.finish(RepositoryAnalysis()).value
    trait_facts = {
        fact.source: fact
        for packet in analysis.packets
        for fact in packet.facts
        if fact.kind == "php-trait-use"
    }

    consumer = trait_facts["Acme\\Model\\Consumer"]
    assert dict(consumer.attributes) == {
        "constructorResolution": "class-method-precedence",
        "resolvedMethod": "__construct",
        "retrievalIdentifier:consumerConstructor": "__construct",
        "retrievalIdentifier:traitConstructor": "__construct",
        "sourceKind": "class",
        "targetKind": "trait",
    }
    assert consumer.related_paths == (
        "app/code/Acme/Model/ProvidesService.php",
    )

    inherited = trait_facts["Acme\\Model\\InheritedConstructor"]
    assert dict(inherited.attributes) == {
        "retrievalIdentifier:traitConstructor": "__construct",
        "sourceKind": "class",
        "targetKind": "trait",
    }

    restored = plugin.restore_repository_analysis(
        "php-trait-constructor-restored",
        analysis.snapshots,
    )
    replay = restored.value.finish(RepositoryAnalysis()).value
    assert replay.packets == analysis.packets


def test_php_repository_does_not_guess_ambiguous_symbol_relations():
    catalog = PluginCatalog.discover(PLUGINS_ROOT)
    plugin = catalog.implementation("php")
    started = plugin.start_repository_analysis("php-ambiguous")
    started.value.ingest((
        FileArtifact(
            "app/code/First/Shared.php",
            "<?php namespace Acme; class Shared {}",
        ),
        FileArtifact(
            "app/code/Second/Shared.php",
            "<?php namespace Acme; class Shared {}",
        ),
        FileArtifact(
            "app/code/Service.php",
            (
                "<?php namespace Acme; class Service extends Shared { "
                "public function run() { new Shared(); Shared::go(); } }"
            ),
        ),
    ))

    analysis = started.value.finish(RepositoryAnalysis()).value

    assert analysis.packets == ()


def test_php_repository_resolves_exact_construction_and_static_call_relations():
    catalog = PluginCatalog.discover(PLUGINS_ROOT)
    plugin = catalog.implementation("php")
    started = plugin.start_repository_analysis("php-code-calls")
    started.value.ingest((
        FileArtifact(
            "app/code/Acme/Model/BaseService.php",
            (
                "<?php namespace Acme\\Model; class BaseService { "
                "public static function prepare() {} }"
            ),
        ),
        FileArtifact(
            "app/code/Acme/Library/Target.php",
            (
                "<?php namespace Acme\\Library; class Target { "
                "public static function boot() {} }"
            ),
        ),
        FileArtifact(
            "app/code/Acme/Model/Service.php",
            r"""<?php
namespace Acme\Model;

use Acme\Library\Target as Alias;

class Service extends BaseService
{
    public function run(): void
    {
        new alias();
        ALIAS::boot();
        Alias::boot();
        parent::prepare();
        self::local();
        static::late();
        new \External\Unknown();
        $dynamic = Alias::class;
        $dynamic::boot();
    }

    private static function local(): void {}
}
""",
        ),
    ))

    analysis = started.value.finish(RepositoryAnalysis()).value
    packet = next(
        packet
        for packet in analysis.packets
        if packet.key == "app/code/Acme/Model/Service.php"
    )
    code_facts = tuple(
        fact
        for fact in packet.facts
        if fact.kind in {
            "php-construction-relation",
            "php-static-call-relation",
        }
    )

    assert len(code_facts) == 3
    assert packet.paths == (
        "app/code/Acme/Library/Target.php",
        "app/code/Acme/Model/BaseService.php",
        "app/code/Acme/Model/Service.php",
    )
    assert {
        (
            fact.kind,
            fact.relation,
            fact.target,
            dict(fact.attributes).get("targetMethod"),
            dict(fact.attributes).get("callerMethod"),
            fact.related_paths,
        )
        for fact in code_facts
    } == {
        (
            "php-construction-relation",
            "constructs",
            "Acme\\Library\\Target",
            None,
            "run",
            ("app/code/Acme/Library/Target.php",),
        ),
        (
            "php-static-call-relation",
            "calls-static",
            "Acme\\Library\\Target",
            "boot",
            "run",
            ("app/code/Acme/Library/Target.php",),
        ),
        (
            "php-static-call-relation",
            "calls-static",
            "Acme\\Model\\BaseService",
            "prepare",
            "run",
            ("app/code/Acme/Model/BaseService.php",),
        ),
    }
    assert not any(
        fact.target in {
            "Acme\\Model\\Service",
            "External\\Unknown",
        }
        for fact in code_facts
    )

    restored = plugin.restore_repository_analysis(
        "php-code-calls-restored",
        analysis.snapshots,
    )
    replay = restored.value.finish(RepositoryAnalysis()).value
    assert replay.packets == analysis.packets


def test_php_repository_resolves_exact_constructor_property_instance_calls():
    catalog = PluginCatalog.discover(PLUGINS_ROOT)
    plugin = catalog.implementation("php")
    started = plugin.start_repository_analysis("php-instance-calls")
    started.value.ingest((
        FileArtifact(
            "app/code/Acme/Api/Contract.php",
            (
                "<?php namespace Acme\\Api; interface Contract { "
                "public function getList(): array; }"
            ),
        ),
        FileArtifact(
            "app/code/Acme/Model/Helper.php",
            (
                "<?php namespace Acme\\Model; class Helper { "
                "public function prepare(): void {} }"
            ),
        ),
        FileArtifact(
            "app/code/Acme/Model/Service.php",
            r"""<?php
namespace Acme\Model;

use Acme\Api\Contract;

class Service
{
    private Contract $contract;

    public function __construct(
        Contract $contract,
        private Helper $helper
    ) {
        $this->contract = $contract;
    }

    public function run(): array
    {
        $this->helper?->prepare();
        return $this->contract->getList();
    }
}
""",
        ),
    ))

    analysis = started.value.finish(RepositoryAnalysis()).value
    packet = next(
        packet
        for packet in analysis.packets
        if packet.key == "app/code/Acme/Model/Service.php"
    )
    instance_facts = tuple(
        fact
        for fact in packet.facts
        if fact.kind == "php-instance-call-relation"
    )

    assert {
        (
            fact.relation,
            fact.target,
            dict(fact.attributes).get("targetMethod"),
            dict(fact.attributes).get("callerMethod"),
            dict(fact.attributes).get("targetMethodDeclared"),
            dict(fact.attributes).get("targetDeclaredReturnType"),
            dict(fact.attributes).get("targetMethodVisibility"),
            fact.related_paths,
        )
        for fact in instance_facts
    } == {
        (
            "calls-instance",
            "Acme\\Api\\Contract",
            "getList",
            "run",
            "true",
            "array",
            "public",
            ("app/code/Acme/Api/Contract.php",),
        ),
        (
            "calls-instance",
            "Acme\\Model\\Helper",
            "prepare",
            "run",
            "true",
            "void",
            "public",
            ("app/code/Acme/Model/Helper.php",),
        ),
    }

    restored = plugin.restore_repository_analysis(
        "php-instance-calls-restored",
        analysis.snapshots,
    )
    replay = restored.value.finish(RepositoryAnalysis()).value
    assert replay.packets == analysis.packets


def test_php_repository_emits_exact_intra_class_method_calls():
    catalog = PluginCatalog.discover(PLUGINS_ROOT)
    plugin = catalog.implementation("php")
    started = plugin.start_repository_analysis("php-intra-class-calls")
    started.value.ingest((
        FileArtifact(
            "app/code/Acme/Model/Service.php",
            r"""<?php
namespace Acme\Model;

class Service
{
    public function run(): array
    {
        return $this->prepare();
    }

    private function prepare(): array
    {
        return [];
    }
}
""",
        ),
    ))

    analysis = started.value.finish(RepositoryAnalysis()).value
    facts = tuple(
        fact
        for packet in analysis.packets
        for fact in packet.facts
        if fact.kind == "php-intra-class-call-relation"
    )

    assert len(facts) == 1
    fact = facts[0]
    attributes = dict(fact.attributes)
    assert fact.source == "Acme\\Model\\Service"
    assert fact.relation == "calls-instance"
    assert fact.target == "Acme\\Model\\Service"
    assert fact.path == "app/code/Acme/Model/Service.php"
    assert fact.line == 8
    assert fact.related_paths == ()
    assert attributes["callerMethod"] == "run"
    assert attributes["targetMethod"] == "prepare"
    assert attributes["receiverResolution"] == "self-instance"
    assert attributes["targetMethodDeclared"] == "true"
    assert attributes["targetMethodVisibility"] == "private"
    assert attributes["targetDeclaredReturnType"] == "array"

    restored = plugin.restore_repository_analysis(
        "php-intra-class-calls-restored",
        analysis.snapshots,
    )
    replay = restored.value.finish(RepositoryAnalysis()).value
    assert replay.packets == analysis.packets


def test_php_repository_resolves_exact_parameter_and_local_instance_calls():
    catalog = PluginCatalog.discover(PLUGINS_ROOT)
    plugin = catalog.implementation("php")
    started = plugin.start_repository_analysis("php-local-instance-calls")
    started.value.ingest((
        FileArtifact(
            "app/code/Acme/Api/Contract.php",
            (
                "<?php namespace Acme\\Api; interface Contract { "
                "public function fetch(): array; "
                "public function save(): void; "
                "public function load(): ?array; }"
            ),
        ),
        FileArtifact(
            "app/code/Acme/Model/Service.php",
            r"""<?php
namespace Acme\Model;

use Acme\Api\Contract;

class Service
{
    public function __construct(private Contract $contract) {}

    public function run(Contract $parameter): void
    {
        $parameter->fetch();
        $alias = $parameter;
        $alias?->load();
        $fromProperty = $this->contract;
        $fromProperty->save();
    }
}
""",
        ),
    ))

    analysis = started.value.finish(RepositoryAnalysis()).value
    facts = tuple(
        fact
        for packet in analysis.packets
        for fact in packet.facts
        if fact.kind == "php-instance-call-relation"
    )

    assert {
        (
            dict(fact.attributes)["targetMethod"],
            dict(fact.attributes)["receiverResolution"],
            dict(fact.attributes).get("targetDeclaredReturnType"),
        )
        for fact in facts
    } == {
        ("fetch", "declared-parameter", "array"),
        ("load", "local-exact-assignment", "?array"),
        ("save", "local-exact-assignment", "void"),
    }


def test_php_repository_resolves_exact_constructed_local_and_direct_receiver():
    catalog = PluginCatalog.discover(PLUGINS_ROOT)
    plugin = catalog.implementation("php")
    started = plugin.start_repository_analysis("php-constructed-instance-calls")
    started.value.ingest((
        FileArtifact(
            "app/code/Acme/Model/Target.php",
            (
                "<?php namespace Acme\\Model; class Target { "
                "public function first(): self {} "
                "public function second(): void {} }"
            ),
        ),
        FileArtifact(
            "app/code/Acme/Model/Service.php",
            r"""<?php
namespace Acme\Model;

class Service
{
    public function run(): void
    {
        $local = new Target();
        $local->first();
        (new Target())->second();
    }
}
""",
        ),
    ))

    analysis = started.value.finish(RepositoryAnalysis()).value
    facts = tuple(
        fact
        for packet in analysis.packets
        for fact in packet.facts
        if fact.kind == "php-instance-call-relation"
    )

    assert {
        (
            dict(fact.attributes)["targetMethod"],
            dict(fact.attributes)["receiverResolution"],
        )
        for fact in facts
    } == {
        ("first", "local-exact-assignment"),
        ("second", "direct-construction"),
    }


def test_php_repository_resolves_exact_declared_call_return_chains():
    catalog = PluginCatalog.discover(PLUGINS_ROOT)
    plugin = catalog.implementation("php")
    started = plugin.start_repository_analysis("php-call-return-chains")
    started.value.ingest((
        FileArtifact(
            "app/code/Acme/Model/Factory.php",
            r"""<?php
namespace Acme\Model;

class Factory
{
    public function create(): Product
    {
        return new Product();
    }
}
""",
        ),
        FileArtifact(
            "app/code/Acme/Model/Product.php",
            r"""<?php
namespace Acme\Model;

class Product
{
    public function getSku(): string
    {
        return 'sku';
    }

    public function result(): Result
    {
        return new Result();
    }
}
""",
        ),
        FileArtifact(
            "app/code/Acme/Model/Result.php",
            r"""<?php
namespace Acme\Model;

class Result
{
    public function save(): void {}
}
""",
        ),
        FileArtifact(
            "app/code/Acme/Model/Service.php",
            r"""<?php
namespace Acme\Model;

class Service
{
    public function __construct(private Factory $factory) {}

    public function run(): void
    {
        $this->factory->create()->getSku();
        $this->factory->create()->result()->save();
    }
}
""",
        ),
    ))

    analysis = started.value.finish(RepositoryAnalysis()).value
    facts = tuple(
        fact
        for packet in analysis.packets
        if packet.key == "app/code/Acme/Model/Service.php"
        for fact in packet.facts
        if fact.kind == "php-instance-call-relation"
    )
    by_method = {
        dict(fact.attributes)["targetMethod"]: fact
        for fact in facts
    }

    assert set(by_method) == {"create", "getSku", "result", "save"}
    assert by_method["create"].target == "Acme\\Model\\Factory"
    assert (
        dict(by_method["create"].attributes)["receiverResolution"]
        == "declared-property"
    )

    get_sku = by_method["getSku"]
    get_sku_attributes = dict(get_sku.attributes)
    assert get_sku.target == "Acme\\Model\\Product"
    assert get_sku_attributes["receiverResolution"] == "exact-call-return"
    assert get_sku_attributes["receiverBaseResolution"] == "declared-property"
    assert get_sku_attributes["receiverCall:0000:sourceType"] == (
        "Acme\\Model\\Factory"
    )
    assert get_sku_attributes["receiverCall:0000:method"] == "create"
    assert get_sku_attributes["receiverCall:0000:declaredReturnType"] == (
        "Acme\\Model\\Product"
    )
    assert get_sku_attributes["receiverCall:0000:methodDeclaredOn"] == (
        "Acme\\Model\\Factory"
    )
    assert get_sku_attributes["targetDeclaredReturnType"] == "string"
    assert get_sku.related_paths == (
        "app/code/Acme/Model/Factory.php",
        "app/code/Acme/Model/Product.php",
    )

    save = by_method["save"]
    save_attributes = dict(save.attributes)
    assert save.target == "Acme\\Model\\Result"
    assert save_attributes["receiverCall:0000:method"] == "create"
    assert save_attributes["receiverCall:0001:sourceType"] == (
        "Acme\\Model\\Product"
    )
    assert save_attributes["receiverCall:0001:method"] == "result"
    assert save_attributes["receiverCall:0001:declaredReturnType"] == (
        "Acme\\Model\\Result"
    )
    assert save_attributes["targetDeclaredReturnType"] == "void"
    assert save.related_paths == (
        "app/code/Acme/Model/Factory.php",
        "app/code/Acme/Model/Product.php",
        "app/code/Acme/Model/Result.php",
    )

    restored = plugin.restore_repository_analysis(
        "php-call-return-chains-restored",
        analysis.snapshots,
    )
    replay = restored.value.finish(RepositoryAnalysis()).value
    assert replay.packets == analysis.packets


def test_php_repository_abstains_from_uncertain_call_return_chains():
    catalog = PluginCatalog.discover(PLUGINS_ROOT)
    plugin = catalog.implementation("php")
    started = plugin.start_repository_analysis("php-uncertain-call-return-chains")
    started.value.ingest((
        FileArtifact(
            "app/code/Acme/Model/Factory.php",
            r"""<?php
namespace Acme\Model;

class Factory
{
    public function nullable(): ?Product {}
    public function union(): Product|Other {}
    public function scalar(): array {}
    public function external(): \External\Product {}
    public function undeclared() {}
    public function lateBound(): static {}
    public function ambiguous(): Duplicate {}
}
""",
        ),
        FileArtifact(
            "app/code/Acme/Model/Product.php",
            (
                "<?php namespace Acme\\Model; class Product { "
                "public function execute(): void {} }"
            ),
        ),
        FileArtifact(
            "app/code/Acme/Model/Other.php",
            "<?php namespace Acme\\Model; class Other {}",
        ),
        FileArtifact(
            "app/code/Acme/Model/DuplicateOne.php",
            "<?php namespace Acme\\Model; class Duplicate {}",
        ),
        FileArtifact(
            "app/code/Acme/Model/DuplicateTwo.php",
            "<?php namespace Acme\\Model; class Duplicate {}",
        ),
        FileArtifact(
            "app/code/Acme/Model/Service.php",
            r"""<?php
namespace Acme\Model;

class Service
{
    public function run(Factory $factory): void
    {
        $factory->nullable()?->execute();
        $factory->union()->execute();
        $factory->scalar()->execute();
        $factory->external()->execute();
        $factory->undeclared()->execute();
        $factory->lateBound()->execute();
        $factory->ambiguous()->execute();
    }
}
""",
        ),
    ))

    analysis = started.value.finish(RepositoryAnalysis()).value
    facts = tuple(
        fact
        for packet in analysis.packets
        if packet.key == "app/code/Acme/Model/Service.php"
        for fact in packet.facts
        if fact.kind == "php-instance-call-relation"
    )

    assert {
        dict(fact.attributes)["targetMethod"]
        for fact in facts
    } == {
        "ambiguous",
        "external",
        "lateBound",
        "nullable",
        "scalar",
        "undeclared",
        "union",
    }
    assert {fact.target for fact in facts} == {"Acme\\Model\\Factory"}
    assert all(
        dict(fact.attributes)["receiverResolution"] == "declared-parameter"
        for fact in facts
    )


def test_php_repository_resolves_direct_self_return_but_not_subclass_assumption():
    catalog = PluginCatalog.discover(PLUGINS_ROOT)
    plugin = catalog.implementation("php")
    started = plugin.start_repository_analysis("php-self-call-return-chain")
    started.value.ingest((
        FileArtifact(
            "app/code/Acme/Model/Factory.php",
            r"""<?php
namespace Acme\Model;

class Factory
{
    public function prepare(): self
    {
        return $this;
    }

    public function create(): Product
    {
        return new Product();
    }
}
""",
        ),
        FileArtifact(
            "app/code/Acme/Model/BaseFactory.php",
            r"""<?php
namespace Acme\Model;

class BaseFactory
{
    public function inheritedPrepare(): self
    {
        return $this;
    }
}
""",
        ),
        FileArtifact(
            "app/code/Acme/Model/ChildFactory.php",
            (
                "<?php namespace Acme\\Model; "
                "class ChildFactory extends BaseFactory { "
                "public function onlyOnChild(): Product {} }"
            ),
        ),
        FileArtifact(
            "app/code/Acme/Model/Product.php",
            (
                "<?php namespace Acme\\Model; class Product { "
                "public function getSku(): string {} }"
            ),
        ),
        FileArtifact(
            "app/code/Acme/Model/Service.php",
            r"""<?php
namespace Acme\Model;

class Service
{
    public function run(Factory $factory, ChildFactory $child): void
    {
        $factory->prepare()->create()->getSku();
        $child->inheritedPrepare()->onlyOnChild();
    }
}
""",
        ),
    ))

    analysis = started.value.finish(RepositoryAnalysis()).value
    facts = tuple(
        fact
        for packet in analysis.packets
        if packet.key == "app/code/Acme/Model/Service.php"
        for fact in packet.facts
        if fact.kind == "php-instance-call-relation"
    )
    fact_keys = {
        (
            dict(fact.attributes)["targetMethod"],
            fact.target,
            dict(fact.attributes)["receiverResolution"],
        )
        for fact in facts
    }

    assert (
        "create",
        "Acme\\Model\\Factory",
        "exact-call-return",
    ) in fact_keys
    assert (
        "getSku",
        "Acme\\Model\\Product",
        "exact-call-return",
    ) in fact_keys
    assert (
        "onlyOnChild",
        "Acme\\Model\\ChildFactory",
        "exact-call-return",
    ) not in fact_keys
    get_sku = next(
        fact for fact in facts
        if dict(fact.attributes)["targetMethod"] == "getSku"
    )
    attributes = dict(get_sku.attributes)
    assert attributes["receiverCall:0000:declaredReturnType"] == "self"
    assert attributes["receiverCall:0000:methodDeclaredOn"] == (
        "Acme\\Model\\Factory"
    )
    assert attributes["receiverCall:0001:declaredReturnType"] == (
        "Acme\\Model\\Product"
    )


def test_php_repository_resolves_unique_parent_method_contract_and_source():
    catalog = PluginCatalog.discover(PLUGINS_ROOT)
    plugin = catalog.implementation("php")
    started = plugin.start_repository_analysis("php-inherited-method-contract")
    started.value.ingest((
        FileArtifact(
            "app/code/Acme/Model/BaseTarget.php",
            r"""<?php
namespace Acme\Model;

class BaseTarget
{
    public function validate(): bool
    {
        return true;
    }
}
""",
        ),
        FileArtifact(
            "app/code/Acme/Model/Target.php",
            (
                "<?php namespace Acme\\Model; "
                "class Target extends BaseTarget {}"
            ),
        ),
        FileArtifact(
            "app/code/Acme/Model/Service.php",
            r"""<?php
namespace Acme\Model;

class Service
{
    public function run(Target $target): bool
    {
        return $target->validate();
    }
}
""",
        ),
    ))

    analysis = started.value.finish(RepositoryAnalysis()).value
    fact = next(
        fact
        for packet in analysis.packets
        for fact in packet.facts
        if fact.kind == "php-instance-call-relation"
    )
    attributes = dict(fact.attributes)

    assert fact.target == "Acme\\Model\\Target"
    assert fact.related_paths == (
        "app/code/Acme/Model/BaseTarget.php",
        "app/code/Acme/Model/Target.php",
    )
    assert attributes["targetMethodDeclared"] == "true"
    assert attributes["targetMethodDeclarationOrigin"] == "inherited-parent"
    assert attributes["targetMethodDeclaredOn"] == "Acme\\Model\\BaseTarget"
    assert attributes["targetDeclaredReturnType"] == "bool"

    restored = plugin.restore_repository_analysis(
        "php-inherited-method-contract-restored",
        analysis.snapshots,
    )
    replay = restored.value.finish(RepositoryAnalysis()).value
    assert replay.packets == analysis.packets


def test_php_repository_abstains_from_private_or_external_parent_method_contract():
    catalog = PluginCatalog.discover(PLUGINS_ROOT)
    plugin = catalog.implementation("php")
    started = plugin.start_repository_analysis(
        "php-unavailable-parent-method-contract"
    )
    started.value.ingest((
        FileArtifact(
            "app/code/Acme/Model/BaseTarget.php",
            r"""<?php
namespace Acme\Model;

class BaseTarget
{
    private function hidden(): array
    {
        return [];
    }
}
""",
        ),
        FileArtifact(
            "app/code/Acme/Model/PrivateTarget.php",
            (
                "<?php namespace Acme\\Model; "
                "class PrivateTarget extends BaseTarget {}"
            ),
        ),
        FileArtifact(
            "app/code/Acme/Model/ExternalTarget.php",
            (
                "<?php namespace Acme\\Model; "
                "class ExternalTarget extends \\External\\BaseTarget {}"
            ),
        ),
        FileArtifact(
            "app/code/Acme/Model/Service.php",
            r"""<?php
namespace Acme\Model;

class Service
{
    public function run(
        PrivateTarget $private,
        ExternalTarget $external
    ): void {
        $private->hidden();
        $external->unknown();
    }
}
""",
        ),
    ))

    analysis = started.value.finish(RepositoryAnalysis()).value
    facts = tuple(
        fact
        for packet in analysis.packets
        for fact in packet.facts
        if fact.kind == "php-instance-call-relation"
    )

    assert len(facts) == 2
    assert all(
        "targetMethodDeclared" not in dict(fact.attributes)
        for fact in facts
    )
    assert all(
        len(fact.related_paths) == 1
        for fact in facts
    )


def test_php_repository_abstains_after_unknown_or_conflicting_local_assignment():
    catalog = PluginCatalog.discover(PLUGINS_ROOT)
    plugin = catalog.implementation("php")
    started = plugin.start_repository_analysis("php-local-instance-ambiguous")
    started.value.ingest((
        FileArtifact(
            "app/code/Acme/Model/First.php",
            (
                "<?php namespace Acme\\Model; class First { "
                "public function execute(): void {} }"
            ),
        ),
        FileArtifact(
            "app/code/Acme/Model/Second.php",
            (
                "<?php namespace Acme\\Model; class Second { "
                "public function execute(): void {} }"
            ),
        ),
        FileArtifact(
            "app/code/Acme/Model/Service.php",
            r"""<?php
namespace Acme\Model;

class Service
{
    public function run(First|Second $union, First $reassigned): void
    {
        $union->execute();
        $dynamic = factory();
        $dynamic->execute();
        $reassigned = factory();
        $reassigned->execute();
        $conflict = new First();
        $conflict = new Second();
        $conflict->execute();
    }

    public function closure(First $captured): void
    {
        $callback = function () use ($captured): void {
            $captured->execute();
        };
    }
}
""",
        ),
    ))

    analysis = started.value.finish(RepositoryAnalysis()).value

    assert not any(
        fact.kind == "php-instance-call-relation"
        for packet in analysis.packets
        for fact in packet.facts
    )


def test_php_repository_does_not_infer_contract_for_undeclared_target_method():
    catalog = PluginCatalog.discover(PLUGINS_ROOT)
    plugin = catalog.implementation("php")
    started = plugin.start_repository_analysis("php-unknown-method-contract")
    started.value.ingest((
        FileArtifact(
            "app/code/Acme/Model/DynamicTarget.php",
            "<?php namespace Acme\\Model; class DynamicTarget {}",
        ),
        FileArtifact(
            "app/code/Acme/Model/Service.php",
            r"""<?php
namespace Acme\Model;

class Service
{
    public function __construct(private DynamicTarget $target) {}

    public function run(): void
    {
        $this->target->providedByParentTraitOrMagic();
    }
}
""",
        ),
    ))

    analysis = started.value.finish(RepositoryAnalysis()).value
    fact = next(
        fact
        for packet in analysis.packets
        for fact in packet.facts
        if fact.kind == "php-instance-call-relation"
    )
    attributes = dict(fact.attributes)

    assert attributes["targetMethod"] == "providedByParentTraitOrMagic"
    assert "targetMethodDeclared" not in attributes
    assert "targetDeclaredReturnType" not in attributes


def test_php_repository_abstains_from_ambiguous_instance_call_receivers():
    catalog = PluginCatalog.discover(PLUGINS_ROOT)
    plugin = catalog.implementation("php")
    started = plugin.start_repository_analysis("php-instance-ambiguous")
    started.value.ingest((
        FileArtifact(
            "app/code/Acme/Api/First.php",
            "<?php namespace Acme\\Api; interface First {}",
        ),
        FileArtifact(
            "app/code/Acme/Api/Second.php",
            "<?php namespace Acme\\Api; interface Second {}",
        ),
        FileArtifact(
            "app/code/Acme/Model/Service.php",
            r"""<?php
namespace Acme\Model;

use Acme\Api\First;
use Acme\Api\Second;

class Service
{
    private First|Second $ambiguous;
    private $dynamic;

    public function __construct($dynamic)
    {
        $this->dynamic = $dynamic;
    }

    public function run(string $property): void
    {
        $this->ambiguous->execute();
        $this->dynamic->execute();
        $this->{$property}->execute();
    }
}
""",
        ),
    ))

    analysis = started.value.finish(RepositoryAnalysis()).value

    assert not any(
        fact.kind == "php-instance-call-relation"
        for packet in analysis.packets
        for fact in packet.facts
    )


def test_php_repository_target_deletion_removes_restored_relation_packet():
    catalog = PluginCatalog.discover(PLUGINS_ROOT)
    plugin = catalog.implementation("php")
    base_path = "app/code/Acme/Model/BaseService.php"
    service_path = "app/code/Acme/Model/Service.php"
    started = plugin.start_repository_analysis("php-delete-base")
    started.value.ingest((
        FileArtifact(
            base_path,
            "<?php namespace Acme\\Model; class BaseService {}",
        ),
        FileArtifact(
            service_path,
            "<?php namespace Acme\\Model; class Service extends BaseService {}",
        ),
    ))
    base = started.value.finish(RepositoryAnalysis()).value
    assert len(base.packets) == 1

    restored = plugin.restore_repository_analysis(
        "php-delete-target",
        base.snapshots,
    )
    restored.value.ingest((FileArtifact(base_path, "", deleted=True),))
    changed = restored.value.finish(RepositoryAnalysis()).value

    assert changed.packets == ()
