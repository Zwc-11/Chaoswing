"""Reranker registry — strategy/registry pattern that lets new rerankers slot
into the benchmark with one decorator.

Every reranker (bi-encoder, fine-tuned cross-encoder, RankGPT, BM25, Cohere)
implements the `Reranker` Protocol and registers itself at import time::

    @register("cross-encoder-finetuned")
    class FineTunedCrossEncoder:
        requires_training = True
        def rerank(self, source, candidates, *, cutoff): ...

`run_rerank_benchmark` then iterates `registry.iter_rerankers()` and produces
a single comparison table. Adding a new method is one file plus one decorator;
no central if/elif chain to update.
"""
from __future__ import annotations

from typing import Callable, Iterator, Protocol, Sequence, runtime_checkable

from chaoswing.ml._types import MarketDoc, ScoredCandidate, TemporalCutoff


@runtime_checkable
class Reranker(Protocol):
    """The contract every reranker satisfies.

    `cutoff` is required on every call: a reranker that uses any data on or
    after the cutoff is leaking. Concrete implementations should either:
      * not consume timestamped data at all (text-only rerankers), or
      * route every timestamped lookup through `chaoswing.ml.leakage` helpers.
    """

    name: str
    requires_training: bool

    def rerank(
        self,
        source: MarketDoc,
        candidates: Sequence[MarketDoc],
        *,
        cutoff: TemporalCutoff,
    ) -> list[ScoredCandidate]: ...


class RerankerRegistry:
    """Process-global registry. Use the module-level `registry` singleton."""

    def __init__(self) -> None:
        self._by_name: dict[str, type] = {}

    def register(self, name: str) -> Callable[[type], type]:
        def decorator(cls: type) -> type:
            if name in self._by_name:
                raise ValueError(f"reranker '{name}' is already registered")
            cls.name = name  # type: ignore[attr-defined]
            self._by_name[name] = cls
            return cls

        return decorator

    def get(self, name: str) -> type:
        try:
            return self._by_name[name]
        except KeyError as exc:
            raise KeyError(
                f"no reranker registered as '{name}'. Known: {sorted(self._by_name)}"
            ) from exc

    def names(self) -> list[str]:
        return sorted(self._by_name)

    def iter_rerankers(self) -> Iterator[tuple[str, type]]:
        for name in self.names():
            yield name, self._by_name[name]


registry = RerankerRegistry()


def register(name: str) -> Callable[[type], type]:
    """Module-level shorthand: `@register("my-method")`."""
    return registry.register(name)
