from __future__ import annotations

import sys
from pathlib import Path

from codecrow_plugins import (
    CandidateClaim,
    FileArtifact,
    OutcomeStatus,
    PluginRegistry,
    ProjectSelector,
    RepositoryFacts,
    ValidationDecision,
    load_descriptor,
)


PLUGINS_ROOT = Path(__file__).resolve().parents[3]
DJANGO_ROOT = PLUGINS_ROOT / "frameworks" / "django"
sys.path.insert(0, str(DJANGO_ROOT / "python"))

from codecrow_plugin_django import create_plugin  # noqa: E402


def _plugin():
    return create_plugin(load_descriptor(DJANGO_ROOT / "plugin.json"))


def _facts(path: str, content: str):
    outcome = _plugin().index_file(FileArtifact(path, content))
    assert outcome.status is OutcomeStatus.HANDLED
    return outcome.value


def test_django_detection_requires_one_coherent_project_root():
    registry = PluginRegistry((
        load_descriptor(PLUGINS_ROOT / "languages" / "python" / "plugin.json"),
        load_descriptor(DJANGO_ROOT / "plugin.json"),
    ))
    selector = ProjectSelector(registry)

    split = selector.select(RepositoryFacts(
        revision="0123456789abcdef",
        paths=tuple(sorted((
            "backend/project/settings.py",
            "backend/project/urls.py",
            "frontend/manage.py",
        ))),
    ))
    coherent = selector.select(RepositoryFacts(
        revision="0123456789abcdef",
        paths=tuple(sorted((
            "services/shop/manage.py",
            "services/shop/project/settings.py",
            "services/shop/project/urls.py",
        ))),
    ))

    assert split.repository_plugins == ("python",)
    assert coherent.repository_plugins == ("python", "django")
    assert "root:services/shop" in coherent.detection_evidence["django"]


def test_django_indexes_settings_apps_middleware_urls_views_models_and_signals():
    settings = _facts("project/settings.py", '''
INSTALLED_APPS = ["shop.apps.ShopConfig"]
MIDDLEWARE = ["django.middleware.security.SecurityMiddleware"]
ROOT_URLCONF = "project.urls"
''')
    urls = _facts("project/urls.py", '''
from django.urls import include, path
from shop.views import OrderView
urlpatterns = [
    path("orders/", OrderView.as_view(), name="orders"),
    path("api/", include("api.urls")),
]
''')
    apps = _facts("shop/apps.py", '''
from django.apps import AppConfig
class ShopConfig(AppConfig):
    name = "shop"
    label = "store"
''')
    models = _facts("shop/models.py", '''
from django.db import models
class Order(models.Model):
    customer = models.ForeignKey("Customer", on_delete=models.CASCADE, related_name="orders")
    tags = models.ManyToManyField("Tag")
''')
    views = _facts("shop/views.py", '''
from django.views import View
class OrderView(View):
    def get(self, request):
        pass
def health(request):
    pass
''')
    signals = _facts("shop/signals.py", '''
from django.db.models.signals import post_save
from django.dispatch import receiver
@receiver(post_save, sender=Order)
def publish_order(sender, **kwargs):
    pass
post_save.connect(publish_order, sender=Order)
''')

    all_facts = (*settings, *urls, *apps, *models, *views, *signals)
    triples = {(fact.kind, fact.relation, fact.target) for fact in all_facts}
    assert ("django-installed-app", "installs", "shop.apps.ShopConfig") in triples
    assert ("django-middleware", "uses", "django.middleware.security.SecurityMiddleware") in triples
    assert ("django-url-configuration", "uses", "project.urls") in triples
    assert ("django-url-route", "dispatches-to", "OrderView.as_view") in triples
    assert ("django-url-include", "includes", "api.urls") in triples
    assert ("django-app-config", "configures", "shop") in triples
    assert ("django-model", "declares", "shop.models.Order") in triples
    assert ("django-model-relation", "many-to-one", "Customer") in triples
    assert ("django-model-relation", "many-to-many", "Tag") in triples
    assert ("django-view", "declares", "shop.views.OrderView") in triples
    assert ("django-view-action", "handles", "GET") in triples
    assert ("django-signal-receiver", "notifies", "shop.signals.publish_order") in triples


def test_django_resolves_import_aliases_for_owned_framework_symbols():
    apps = _facts("shop/apps.py", '''
from django.apps.config import AppConfig as DjangoConfig
class ShopConfig(DjangoConfig):
    name = "shop"
''')
    models = _facts("shop/models.py", '''
from django.db.models import CharField as Text, ForeignKey as BelongsTo, Model as DjangoModel
from django.db.models.deletion import CASCADE
class Order(DjangoModel):
    reference = Text(max_length=20)
    customer = BelongsTo("Customer", on_delete=CASCADE)
''')
    urls = _facts("project/urls.py", '''
from django.urls import include as nest, path as route, re_path as regex
from shop.views import OrderView
urlpatterns = [
    route("orders/", OrderView.as_view()),
    regex(r"^api/", nest("api.urls")),
]
''')
    views = _facts("shop/views.py", '''
from django.views.decorators.http import require_GET as only_get
from django.views.generic import DetailView as DjangoDetailView
from rest_framework.viewsets import ModelViewSet as RestModelViewSet
class OrderDetail(DjangoDetailView):
    def get(self, request):
        pass
class OrderApi(RestModelViewSet):
    def post(self, request):
        pass
@only_get
def health(request):
    pass
''')
    signals = _facts("shop/signals.py", '''
from django.db.models.signals import post_save as saved
from django.dispatch import Signal as Event, receiver as listens
@listens(saved, sender=Order)
def publish_order(sender, **kwargs):
    pass
order_published = Event()
def publish_custom(sender, **kwargs):
    pass
order_published.connect(publish_custom)
''')

    all_facts = (*apps, *models, *urls, *views, *signals)
    triples = {(fact.kind, fact.relation, fact.target) for fact in all_facts}
    assert ("django-app-config", "configures", "shop") in triples
    assert ("django-model", "declares", "shop.models.Order") in triples
    assert ("django-model-field", "declares", "shop.models.Order.reference") in triples
    assert ("django-model-relation", "many-to-one", "Customer") in triples
    assert ("django-url-route", "dispatches-to", "OrderView.as_view") in triples
    assert ("django-url-include", "includes", "api.urls") in triples
    assert ("django-view", "declares", "shop.views.OrderDetail") in triples
    assert ("django-view", "declares", "shop.views.OrderApi") in triples
    assert ("django-view", "declares", "shop.views.health") in triples
    assert ("django-signal-receiver", "notifies", "shop.signals.publish_order") in triples
    assert ("django-signal-receiver", "notifies", "shop.signals.publish_custom") in triples


def test_django_abstains_from_same_named_local_and_third_party_constructs():
    outcome = _plugin().index_file(FileArtifact("shop/views.py", '''
from eventbus import post_save, receiver
from records import Model, TextField
from tables import TableView
class Order(Model):
    label = TextField()
class OrderTable(TableView):
    def get(self, request):
        pass
def health(request):
    pass
@receiver(post_save)
def publish_order(sender, **kwargs):
    pass
post_save.connect(publish_order)
'''))

    assert outcome.status is OutcomeStatus.ABSTAINED


def test_django_abstains_from_unproven_url_helpers_and_dynamic_aliases():
    outcome = _plugin().index_file(FileArtifact("project/urls.py", '''
from django.urls import path
def include(module):
    return module
route = path
urlpatterns = [
    route("orders/", order_view),
    path("api/", include("api.urls")),
]
'''))

    assert outcome.status is OutcomeStatus.ABSTAINED


def test_django_skips_symbolic_settings_and_route_values():
    settings = _facts("project/settings.py", '''
APP_NAME = "dynamic.app"
MIDDLEWARE_NAME = "dynamic.Middleware"
URLCONF = "dynamic.urls"
INSTALLED_APPS = [APP_NAME, "shop.apps.ShopConfig"]
MIDDLEWARE = [MIDDLEWARE_NAME, "django.middleware.security.SecurityMiddleware"]
ROOT_URLCONF = URLCONF
''')
    urls = _facts("project/urls.py", '''
from django.urls import path
from shop.views import OrderView
PREFIX = "dynamic/"
urlpatterns = [
    path(PREFIX, OrderView.as_view()),
    path("orders/", OrderView.as_view()),
]
''')

    triples = {(fact.kind, fact.target) for fact in (*settings, *urls)}
    assert ("django-installed-app", "shop.apps.ShopConfig") in triples
    assert ("django-middleware", "django.middleware.security.SecurityMiddleware") in triples
    assert ("django-url-route", "OrderView.as_view") in triples
    assert not any(fact.target == "APP_NAME" for fact in settings)
    assert not any(fact.target == "MIDDLEWARE_NAME" for fact in settings)
    assert not any(fact.kind == "django-url-configuration" for fact in settings)
    assert not any(fact.source.endswith(":PREFIX") for fact in urls)


def test_django_requires_owned_model_fields_and_static_function_view_decorators():
    facts = _facts("shop/views.py", '''
from django.db import models
from django.views.decorators.http import require_GET
from toolkit import view_decorator
class Order(models.Model):
    owned = models.CharField(max_length=20)
    unproven = CustomField()
def undecorated(request):
    pass
@view_decorator
def third_party_decorated(request):
    pass
@require_GET
def health(request):
    pass
''')

    targets = {(fact.kind, fact.target) for fact in facts}
    assert ("django-model-field", "shop.views.Order.owned") in targets
    assert ("django-model-field", "shop.views.Order.unproven") not in targets
    assert ("django-view", "shop.views.undecorated") not in targets
    assert ("django-view", "shop.views.third_party_decorated") not in targets
    assert ("django-view", "shop.views.health") in targets


def test_django_abstains_after_framework_imports_or_custom_signals_are_rebound():
    outcome = _plugin().index_file(FileArtifact("shop/views.py", '''
from django.db import models
from django.dispatch import Signal
from django.views.decorators.http import require_GET
models = record_library
require_GET = custom_decorator
event = Signal()
event = event_bus
class Order(models.Model):
    label = models.CharField(max_length=20)
@require_GET
def health(request):
    pass
event.connect(health)
'''))

    assert outcome.status is OutcomeStatus.ABSTAINED


def test_django_abstains_after_conditional_module_rebinding():
    outcome = _plugin().index_file(FileArtifact("shop/models.py", '''
from django.db import models
if use_alternate_records:
    models = record_library
class Order(models.Model):
    label = models.CharField(max_length=20)
'''))

    assert outcome.status is OutcomeStatus.ABSTAINED


def test_django_signal_connect_respects_nested_scope_shadowing():
    shadowed = _plugin().index_file(FileArtifact("shop/apps.py", '''
from django.db.models.signals import post_save
def ready(post_save):
    post_save.connect(handler)
'''))
    proven = _facts("shop/apps.py", '''
from django.db.models.signals import post_save
def ready():
    post_save.connect(handler)
''')

    assert shadowed.status is OutcomeStatus.ABSTAINED
    assert any(
        fact.kind == "django-signal-receiver"
        and fact.target == "shop.apps.handler"
        for fact in proven
    )


def test_django_does_not_treat_an_arbitrary_connect_call_as_a_signal():
    outcome = _plugin().index_file(FileArtifact(
        "shop/services.py",
        "client.connect(handler, sender=Order)\n",
    ))

    assert outcome.status is OutcomeStatus.ABSTAINED


def test_django_does_not_treat_arbitrary_connect_as_signal_even_in_signals_module():
    outcome = _plugin().index_file(FileArtifact(
        "shop/signals.py",
        "from eventbus import order_saved\norder_saved.connect(handler)\n",
    ))

    assert outcome.status is OutcomeStatus.ABSTAINED


def test_django_validation_rejects_only_a_relevant_contradicted_absence():
    model_fact = next(
        fact for fact in _facts("shop/models.py", '''
from django.db import models
class Order(models.Model):
    pass
''')
        if fact.kind == "django-model"
    )
    rejected = _plugin().validate(CandidateClaim(
        category="django-model",
        claim_kind="django-model",
        path="shop/models.py",
        line=2,
        message="The Django model shop.models.Order is missing.",
        evidence=(model_fact,),
    ))
    contextual = _plugin().validate(CandidateClaim(
        category="django-model",
        claim_kind="django-model",
        path="shop/models.py",
        line=2,
        message="The Order model may persist the wrong state.",
        evidence=(model_fact,),
    ))

    assert rejected.value.decision is ValidationDecision.REJECT
    assert rejected.value.code == "django-absence-contradicted"
    assert contextual.value.decision is ValidationDecision.INSUFFICIENT_EVIDENCE
    assert contextual.value.code == "django-topology-not-defect-proof"


def test_django_validation_does_not_match_get_inside_an_unrelated_word():
    action_fact = next(
        fact for fact in _facts("shop/views.py", '''
from django.views import View
class OrderView(View):
    def get(self, request):
        pass
''')
        if fact.kind == "django-view-action"
    )
    result = _plugin().validate(CandidateClaim(
        category="django-view-action",
        claim_kind="django-view-action",
        path="shop/views.py",
        line=3,
        message="The target widget is missing.",
        evidence=(action_fact,),
    ))

    assert result.value.decision is ValidationDecision.INSUFFICIENT_EVIDENCE
    assert result.value.code == "django-cited-identifier-mismatch"


def test_django_validation_does_not_bind_an_unrelated_absence_to_a_model():
    model_fact = next(
        fact for fact in _facts("shop/models.py", '''
from django.db import models
class Order(models.Model):
    pass
''')
        if fact.kind == "django-model"
    )
    unrelated = _plugin().validate(CandidateClaim(
        category="django-model",
        claim_kind="django-model",
        path="shop/models.py",
        line=2,
        message="Order fails closed when its cache entry is missing.",
        evidence=(model_fact,),
    ))
    unknown = _plugin().validate(CandidateClaim(
        category="django-cache",
        claim_kind="django-cache",
        path="shop/models.py",
        line=2,
        message="The Order cache is missing.",
        evidence=(model_fact,),
    ))

    assert unrelated.value.decision is ValidationDecision.INSUFFICIENT_EVIDENCE
    assert unrelated.value.code == "django-topology-not-defect-proof"
    assert unknown.value.decision is ValidationDecision.INSUFFICIENT_EVIDENCE
    assert unknown.value.code == "django-unknown-fact-kind"


def test_django_direct_indexing_and_review_contributions_are_bounded():
    fields = "\n".join(
        f"    field_{index} = models.CharField(max_length=20)"
        for index in range(300)
    )
    facts = _facts(
        "shop/models.py",
        "from django.db import models\nclass Large(models.Model):\n" + fields,
    )
    review = _plugin().review(tuple(f"app_{index}/models.py" for index in range(100)))

    assert len(facts) == 160
    assert {fact.kind for fact in facts} == {"django-model", "django-model-field"}
    assert len(review.value.evidence_requests) == 40
