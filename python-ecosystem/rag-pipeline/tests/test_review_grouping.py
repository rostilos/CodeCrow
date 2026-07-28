from rag_pipeline.core.review_grouping import (
    review_groups_from_architecture_payloads,
)


def test_review_groups_project_only_changed_paths_from_neutral_graph_facts():
    payloads = [{
        "architecture_plugin": "magento",
        "plugin_graph_facts": [
            {
                "kind": "magento-di-effective-preference",
                "path": "app/code/Acme/Checkout/etc/di.xml",
                "related_paths": [
                    "app/code/Acme/Checkout/Api/CartInterface.php",
                    "app/code/Acme/Checkout/Model/Cart.php",
                    "vendor/magento/framework/ObjectManager.php",
                ],
            },
            {
                "kind": "magento-webapi-acl",
                "path": "app/code/Acme/Checkout/etc/webapi.xml",
                "related_paths": [
                    "app/code/Acme/Checkout/etc/acl.xml",
                ],
            },
        ],
    }]
    changed = [
        "app/code/Acme/Checkout/Model/Cart.php",
        "app/code/Acme/Checkout/etc/di.xml",
        "app/code/Acme/Checkout/etc/webapi.xml",
        "app/code/Acme/Checkout/etc/acl.xml",
    ]

    assert review_groups_from_architecture_payloads(payloads, changed) == [
        [
            "app/code/Acme/Checkout/Model/Cart.php",
            "app/code/Acme/Checkout/etc/di.xml",
        ],
        [
            "app/code/Acme/Checkout/etc/acl.xml",
            "app/code/Acme/Checkout/etc/webapi.xml",
        ],
    ]


def test_review_groups_merge_overlapping_facts_deterministically():
    payloads = [
        {
            "plugin_graph_facts": [{
                "path": r"\app\code\Acme\etc\events.xml",
                "related_paths": ["app/code/Acme/Observer/First.php"],
            }],
        },
        {
            "plugin_graph_facts": [{
                "path": "app/code/Acme/Observer/First.php",
                "related_paths": ["app/code/Acme/Observer/Second.php"],
            }],
        },
    ]
    changed = [
        "app/code/Acme/Observer/Second.php",
        "app/code/Acme/etc/events.xml",
        "app/code/Acme/Observer/First.php",
        "unrelated.php",
    ]

    assert review_groups_from_architecture_payloads(payloads, changed) == [[
        "app/code/Acme/Observer/First.php",
        "app/code/Acme/Observer/Second.php",
        "app/code/Acme/etc/events.xml",
    ]]


def test_review_groups_ignore_single_changed_endpoint_and_malformed_facts():
    payloads = [
        {
            "plugin_graph_facts": [
                {
                    "path": "changed.php",
                    "related_paths": ["repository-only.php"],
                },
                "not-a-fact",
            ],
        },
        {"plugin_graph_facts": "not-a-list"},
    ]

    assert review_groups_from_architecture_payloads(
        payloads,
        ["changed.php", "other.php"],
    ) == []
