"""Shared failures for exact repository-index generation binding."""


class ExactIndexPreconditionError(RuntimeError):
    """The requested exact structural generation cannot be safely used."""


class RepositoryDeltaRebuildRequired(ExactIndexPreconditionError):
    """The exact source is usable, but cannot be derived from its base graph."""
