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
RAILS_ROOT = PLUGINS_ROOT / "frameworks" / "rails"
sys.path.insert(0, str(RAILS_ROOT / "python"))

from codecrow_plugin_rails import create_plugin  # noqa: E402


def _plugin():
    return create_plugin(load_descriptor(RAILS_ROOT / "plugin.json"))


def _facts(path: str, content: str):
    outcome = _plugin().index_file(FileArtifact(path, content))
    assert outcome.status is OutcomeStatus.HANDLED
    return outcome.value


def test_rails_detection_requires_one_coherent_project_root():
    registry = PluginRegistry((
        load_descriptor(PLUGINS_ROOT / "languages" / "ruby" / "plugin.json"),
        load_descriptor(RAILS_ROOT / "plugin.json"),
    ))
    selector = ProjectSelector(registry)
    marker = 'source "https://rubygems.org"\ngem "rails"\n'

    split = selector.select(RepositoryFacts(
        revision="0123456789abcdef",
        paths=("backend/config/routes.rb", "frontend/Gemfile"),
        marker_contents={"frontend/Gemfile": marker},
    ))
    coherent = selector.select(RepositoryFacts(
        revision="0123456789abcdef",
        paths=("services/shop/Gemfile", "services/shop/config/routes.rb"),
        marker_contents={"services/shop/Gemfile": marker},
    ))

    assert split.repository_plugins == ("ruby",)
    assert coherent.repository_plugins == ("ruby", "rails")
    assert "root:services/shop" in coherent.detection_evidence["rails"]


def test_rails_engine_detection_does_not_combine_pattern_evidence_across_roots():
    registry = PluginRegistry((
        load_descriptor(PLUGINS_ROOT / "languages" / "ruby" / "plugin.json"),
        load_descriptor(RAILS_ROOT / "plugin.json"),
    ))
    selector = ProjectSelector(registry)
    split = selector.select(RepositoryFacts(
        revision="0123456789abcdef",
        paths=(
            "a/example.gemspec",
            "b/config/routes.rb",
            "c/lib/example/engine.rb",
        ),
        marker_contents={
            "c/lib/example/engine.rb": "class Example < Rails::Engine\nend\n",
        },
    ))
    coherent = selector.select(RepositoryFacts(
        revision="0123456789abcdef",
        paths=(
            "services/blog/blog.gemspec",
            "services/blog/config/routes.rb",
            "services/blog/lib/blog/engine.rb",
        ),
        marker_contents={
            "services/blog/lib/blog/engine.rb": "class Blog < Rails::Engine\nend\n",
        },
    ))
    nested_but_not_root_relative = selector.select(RepositoryFacts(
        revision="0123456789abcdef",
        paths=(
            "services/blog/config/routes.rb",
            "services/blog/nested/blog.gemspec",
            "services/blog/vendor/lib/blog/engine.rb",
        ),
        marker_contents={
            "services/blog/vendor/lib/blog/engine.rb": "class Blog < Rails::Engine\nend\n",
        },
    ))

    assert split.repository_plugins == ("ruby",)
    assert coherent.repository_plugins == ("ruby", "rails")
    assert "root:services/blog" in coherent.detection_evidence["rails"]
    assert nested_but_not_root_relative.repository_plugins == ("ruby",)


def test_rails_indexes_routes_controllers_models_callbacks_associations_and_jobs():
    routes = _facts("config/routes.rb", '''
Rails.application.routes.draw do
  namespace :admin do
    resources :users, only: [:index, :show]
    get "health", to: "health#show"
    root "dashboard#index"
  end
  root "home#index"
end
''')
    controller = _facts("app/controllers/admin/users_controller.rb", '''
module Admin
  class UsersController < ApplicationController
    before_action :authenticate!, only: [:show]
    def index; end
    def show; end
    private
    def helper; end
  end
end
''')
    model = _facts("app/models/order.rb", '''
class Order < ApplicationRecord
  belongs_to :customer, optional: true
  has_many :items, class_name: "LineItem", dependent: :destroy
  before_save :normalize_total
  after_commit :publish, on: :create
end
''')
    job = _facts("app/jobs/import_job.rb", '''
class ImportJob < ApplicationJob
  queue_as :low
  retry_on Timeout::Error, attempts: 3
  def perform(account_id); end
end
''')

    all_facts = (*routes, *controller, *model, *job)
    triples = {(fact.kind, fact.relation, fact.target) for fact in all_facts}
    assert ("rails-route", "declares", "RESOURCES /admin/users") in triples
    assert ("rails-route", "handles", "GET /admin/health") in triples
    assert ("rails-route", "handles", "GET /admin") in triples
    assert ("rails-route", "handles", "GET /") in triples
    assert ("rails-controller", "declares", "Admin::UsersController") in triples
    assert ("rails-controller-action", "exposes", "Admin::UsersController#index") in triples
    assert ("rails-controller-action", "exposes", "Admin::UsersController#show") in triples
    assert ("rails-controller-action", "exposes", "Admin::UsersController#helper") not in triples
    assert ("rails-model", "declares", "Order") in triples
    assert ("rails-association", "belongs-to", "customer") in triples
    assert ("rails-association", "has-many", "items") in triples
    assert ("rails-callback", "registers", "normalize_total") in triples
    assert ("rails-callback", "registers", "publish") in triples
    assert ("rails-job", "declares", "ImportJob") in triples
    assert ("rails-job-queue", "queues-on", "low") in triples
    assert ("rails-job-policy", "retry-on", "Timeout::Error") in triples
    assert ("rails-job-perform", "executes", "ImportJob#perform") in triples


def test_rails_abstains_for_unrelated_ruby_classes_even_in_a_selected_project():
    outcome = _plugin().index_file(FileArtifact(
        "app/models/value.rb",
        "class Value < DataRecord\nend\n",
    ))

    assert outcome.status is OutcomeStatus.ABSTAINED


def test_rails_abstains_for_routes_under_unresolved_resource_prefixes():
    facts = _facts("config/routes.rb", '''
Rails.application.routes.draw do
  resources :users do
    get :profile, on: :member
    resources :posts
  end
end
''')

    targets = {fact.target for fact in facts if fact.kind == "rails-route"}
    assert "RESOURCES /users" in targets
    assert not any("profile" in target or "posts" in target for target in targets)


def test_rails_requires_route_dsl_to_be_inside_a_routes_draw_block():
    outcome = _plugin().index_file(FileArtifact("config/routes.rb", '''
get "outside", to: "outside#show"
draw do
  get "plain-draw", to: "plain#show"
end
routes.draw do
  get "receiver-without-owner", to: "plain#show"
end
client.routes.draw do
  get "dynamic-owner", to: "plain#show"
end
Client.routes.draw do
  get "unproven-constant-owner", to: "plain#show"
end
'''))

    assert outcome.status is OutcomeStatus.ABSTAINED


def test_rails_accepts_canonical_and_static_constant_route_set_owners():
    facts = _facts("config/routes.rb", '''
Rails.application.routes.draw do
  get "canonical", to: "health#show"
end
Shop::Engine.routes.draw do
  get "engine", to: "shop#show"
end
Legacy::Application.routes.draw do
  get "application", to: "legacy#show"
end
''')

    targets = {fact.target for fact in facts if fact.kind == "rails-route"}
    assert targets == {"GET /application", "GET /canonical", "GET /engine"}


def test_rails_ignores_receiver_qualified_route_and_model_lookalikes():
    routes = _facts("config/routes.rb", '''
Rails.application.routes.draw do
  client.get "fake", to: "fake#show"
  client.resources :fake_records
  self.get "health", to: "health#show"
end
''')
    model = _facts("app/models/order.rb", '''
class Order < ApplicationRecord
  client.has_many :fake_items
  client.before_save :fake_callback
  self.has_many :items
  self.before_save :normalize_total
end
''')

    triples = {
        (fact.kind, fact.relation, fact.target)
        for fact in (*routes, *model)
    }
    assert ("rails-route", "handles", "GET /health") in triples
    assert ("rails-association", "has-many", "items") in triples
    assert ("rails-callback", "registers", "normalize_total") in triples
    assert not any("fake" in target for _, _, target in triples)


def test_rails_uses_only_static_resource_path_overrides():
    facts = _facts("config/routes.rb", '''
Rails.application.routes.draw do
  resources :photos, path: "images"
  resources :reports, path: dynamic_segment
end
''')

    targets = {fact.target for fact in facts if fact.kind == "rails-route"}
    assert "RESOURCES /images" in targets
    assert not any("photos" in target or "reports" in target for target in targets)


def test_rails_skips_symbolic_route_paths_prefixes_and_verbs():
    facts = _facts("config/routes.rb", '''
Rails.application.routes.draw do
  path_value = "dynamic"
  get path_value, to: "dynamic#show"
  namespace path_value do
    get "nested", to: "dynamic#nested"
  end
  scope path: path_value do
    get "scoped", to: "dynamic#scoped"
  end
  match "unknown-verb", via: METHODS, to: "dynamic#match"
  get "health", to: "health#show"
end
''')

    targets = {fact.target for fact in facts if fact.kind == "rails-route"}
    assert targets == {"GET /health"}


def test_rails_symbolic_visibility_does_not_expose_hidden_controller_actions():
    facts = _facts("app/controllers/users_controller.rb", '''
class UsersController < ApplicationController
  def index; end
  def secret; end
  private :secret
end
''')

    actions = {fact.target for fact in facts if fact.kind == "rails-controller-action"}
    assert "UsersController#index" in actions
    assert "UsersController#secret" not in actions


def test_rails_validation_rejects_only_a_relevant_contradicted_absence():
    association = next(
        fact for fact in _facts("app/models/order.rb", '''
class Order < ApplicationRecord
  has_many :items
end
''')
        if fact.kind == "rails-association"
    )
    rejected = _plugin().validate(CandidateClaim(
        category="rails-association",
        claim_kind="rails-association",
        path="app/models/order.rb",
        line=2,
        message="Order has no association named items.",
        evidence=(association,),
    ))
    contextual = _plugin().validate(CandidateClaim(
        category="rails-association",
        claim_kind="rails-association",
        path="app/models/order.rb",
        line=2,
        message="The items association may load too much data.",
        evidence=(association,),
    ))

    assert rejected.value.decision is ValidationDecision.REJECT
    assert rejected.value.code == "rails-absence-contradicted"
    assert contextual.value.decision is ValidationDecision.INSUFFICIENT_EVIDENCE
    assert contextual.value.code == "rails-topology-not-defect-proof"


def test_rails_validation_does_not_match_get_inside_an_unrelated_word():
    route = next(
        fact for fact in _facts("config/routes.rb", '''
Rails.application.routes.draw do
  get "health", to: "health#show"
end
''')
        if fact.kind == "rails-route"
    )
    result = _plugin().validate(CandidateClaim(
        category="rails-route",
        claim_kind="rails-route",
        path="config/routes.rb",
        line=2,
        message="The target widget route is missing.",
        evidence=(route,),
    ))

    assert result.value.decision is ValidationDecision.INSUFFICIENT_EVIDENCE
    assert result.value.code == "rails-cited-identifier-mismatch"


def test_rails_validation_does_not_bind_an_unrelated_absence_to_a_model():
    model_fact = next(
        fact for fact in _facts("app/models/order.rb", '''
class Order < ApplicationRecord
end
''')
        if fact.kind == "rails-model"
    )
    unrelated = _plugin().validate(CandidateClaim(
        category="rails-model",
        claim_kind="rails-model",
        path="app/models/order.rb",
        line=1,
        message="Order fails closed when its cache entry is missing.",
        evidence=(model_fact,),
    ))
    unknown = _plugin().validate(CandidateClaim(
        category="rails-service",
        claim_kind="rails-service",
        path="app/models/order.rb",
        line=1,
        message="The Order service is missing.",
        evidence=(model_fact,),
    ))

    assert unrelated.value.decision is ValidationDecision.INSUFFICIENT_EVIDENCE
    assert unrelated.value.code == "rails-topology-not-defect-proof"
    assert unknown.value.decision is ValidationDecision.INSUFFICIENT_EVIDENCE
    assert unknown.value.code == "rails-unknown-fact-kind"


def test_rails_direct_indexing_and_review_contributions_are_bounded():
    routes = "\n".join(
        f'  get "item-{index}", to: "items#show"'
        for index in range(300)
    )
    facts = _facts(
        "config/routes.rb",
        "Rails.application.routes.draw do\n" + routes + "\nend\n",
    )
    review = _plugin().review(tuple(f"app/models/model_{index}.rb" for index in range(100)))

    assert len(facts) == 160
    assert {fact.kind for fact in facts} == {"rails-route"}
    assert len(review.value.evidence_requests) == 40
