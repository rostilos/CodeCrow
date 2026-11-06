from types import SimpleNamespace
from llm import inference_observer as observer


def test_public_default_does_not_change_model_or_error(monkeypatch):
    monkeypatch.delenv('INFERENCE_OBSERVER_FACTORY', raising=False)
    observer._factory.cache_clear()
    model = SimpleNamespace(callbacks=None)
    assert observer.observe_model(model, project_id=1, operation='review', model='x', provider='p', api_key='key') is model
    assert model.callbacks is None
    assert observer.filter_host_error('missing repository') == 'missing repository'


def test_optional_observer_preserves_callbacks_and_failure_does_not_block_model(monkeypatch):
    existing, callback = object(), object()
    monkeypatch.setattr(observer, '_factory', lambda: lambda **kwargs: callback)
    model = SimpleNamespace(callbacks=[existing])
    observer.observe_model(model, project_id=1, operation='qa_documentation', model='x', provider='p', api_key='key')
    assert model.callbacks == [existing, callback]
    def broken(): raise RuntimeError('unavailable')
    monkeypatch.setattr(observer, '_factory', broken)
    assert observer.observe_model(model, project_id=1, operation='command', model='x', provider='p', api_key='key') is model
