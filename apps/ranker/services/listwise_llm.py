"""Module 5 — RankGPT-style listwise LLM reranker.

Algorithm (Sun et al. 2023): prompt the LLM with the source query + a
numbered candidate list, ask for a permutation, parse it back. For lists
larger than `window_size`, slide a window from the bottom up — each window
reranks W candidates and the LLM-chosen top portion is promoted upward.

Framing (build plan §7 + Ranker.md §"Don't"): this is a **generative
baseline**, not the headline. We measure it against the fine-tuned
cross-encoder to make the trade-off visible (LLM = strong zero-shot,
slow + expensive per query; fine-tuned cross-encoder = fast + cheap, the
headline result).

Leakage:
  * The LLM ingests text only — titles and descriptions, no timestamps.
  * `_gate_candidates(cutoff)` runs *before* any API call, same chokepoint
    as every other reranker.
"""
from __future__ import annotations

import logging
import os
import re
from collections.abc import Sequence
from typing import Protocol

from chaoswing.ml._types import MarketDoc, ScoredCandidate, TemporalCutoff
from chaoswing.ml.leakage import assert_before_cutoff

from apps.ranker.services._registry import register


logger = logging.getLogger(__name__)


DEFAULT_MODEL = "claude-haiku-4-5"
DEFAULT_MAX_TOKENS = 400
DEFAULT_WINDOW_SIZE = 20
DEFAULT_STRIDE = 10

SYSTEM_PROMPT = (
    "You are a passage ranking assistant. You will receive a search query and "
    "a numbered list of candidate passages. Output ONLY the reordered list of "
    "passage identifiers from most to least relevant, in the format "
    "[3] > [1] > [4] > [2]. Output nothing else — no explanation, no prose."
)


# ---------------------------------------------------------------------------
# LLM client abstraction
# ---------------------------------------------------------------------------


class LLMClient(Protocol):
    """Narrow surface so tests can inject deterministic clients."""

    def complete(self, *, system: str, user: str, max_tokens: int) -> str: ...


class AnthropicLLMClient:
    """Production client. Lazy-imports `anthropic`.

    Reads model + key from Django settings; falls back to environment
    variables. The cross-encoder benchmark + Cohere baseline use the same
    "fail loudly if unconfigured" pattern.
    """

    def __init__(self, *, model: str | None = None, api_key: str | None = None):
        self._explicit_model = model
        self._explicit_key = api_key
        self._client = None
        self._resolved_model: str | None = None

    @property
    def model(self) -> str:
        if self._resolved_model:
            return self._resolved_model
        if self._explicit_model:
            self._resolved_model = self._explicit_model
        else:
            try:
                from django.conf import settings
                self._resolved_model = (
                    getattr(settings, "CHAOSWING_ANTHROPIC_MODEL", "") or DEFAULT_MODEL
                )
            except Exception:
                self._resolved_model = DEFAULT_MODEL
        return self._resolved_model

    def _resolve_api_key(self) -> str | None:
        if self._explicit_key:
            return self._explicit_key
        try:
            from django.conf import settings
            key = getattr(settings, "CHAOSWING_ANTHROPIC_API_KEY", "")
            if key:
                return key
        except Exception:
            pass
        return os.environ.get("ANTHROPIC_API_KEY")

    def _get_client(self):
        if self._client is not None:
            return self._client
        api_key = self._resolve_api_key()
        if not api_key:
            raise RuntimeError(
                "Anthropic API key not configured. Set CHAOSWING_ANTHROPIC_API_KEY "
                "in Django settings or ANTHROPIC_API_KEY in the environment."
            )
        try:
            from anthropic import Anthropic
        except ImportError as exc:  # pragma: no cover - base dep, should always be present
            raise RuntimeError("anthropic package is not installed") from exc
        self._client = Anthropic(api_key=api_key)
        return self._client

    def complete(self, *, system: str, user: str, max_tokens: int) -> str:
        client = self._get_client()
        try:
            from anthropic.types import TextBlock
        except ImportError:  # pragma: no cover
            TextBlock = None  # type: ignore[assignment]
        response = client.messages.create(
            model=self.model,
            max_tokens=max_tokens,
            temperature=0.0,  # deterministic permutations
            system=system,
            messages=[{"role": "user", "content": user}],
        )
        if TextBlock is not None:
            return "".join(b.text for b in response.content if isinstance(b, TextBlock))
        # Fallback for older anthropic SDK shapes.
        return "".join(getattr(b, "text", "") for b in response.content)


# ---------------------------------------------------------------------------
# Permutation parser
# ---------------------------------------------------------------------------


_BRACKET_RE = re.compile(r"\[(\d+)\]")


def parse_permutation(raw: str, *, expected_count: int) -> list[int]:
    """Extract a permutation of `0..expected_count-1` from a raw LLM response.

    Accepts the canonical `[3] > [1] > [4] > [2]` format and any reasonable
    variant containing `[N]` tokens. Returns 0-indexed positions.

    Robustness:
      * Duplicate `[N]`s after the first occurrence are ignored.
      * Out-of-range identifiers (LLM hallucinated `[99]`) are dropped.
      * If fewer than `expected_count` valid identifiers are found, the
        missing ones are appended in their original order. This is the
        "graceful degradation to identity" path: a malformed response leaves
        the original ranking intact rather than corrupting it.
    """
    found: list[int] = []
    seen: set[int] = set()
    for match in _BRACKET_RE.finditer(raw):
        idx = int(match.group(1)) - 1  # LLM uses 1-indexed
        if idx < 0 or idx >= expected_count or idx in seen:
            continue
        seen.add(idx)
        found.append(idx)
    if len(found) < expected_count:
        for i in range(expected_count):
            if i not in seen:
                found.append(i)
    return found


# ---------------------------------------------------------------------------
# Reranker
# ---------------------------------------------------------------------------


def _gate_candidates(candidates: Sequence[MarketDoc], cutoff: TemporalCutoff) -> None:
    for cand in candidates:
        if cand.first_seen is not None:
            assert_before_cutoff(
                cand.first_seen, cutoff, what=f"candidate {cand.market_id}.first_seen"
            )


def _build_user_prompt(source: MarketDoc, candidates: Sequence[MarketDoc]) -> str:
    """Format the source + numbered candidate list for the LLM."""
    lines = [
        f"Search query: {source.text.strip()}",
        "",
        "Passages:",
    ]
    for i, c in enumerate(candidates, start=1):
        lines.append(f"[{i}] {c.text.strip()}")
    lines.append("")
    lines.append(
        f"Rank the {len(candidates)} passages above from most to least relevant "
        "to the search query. Output only the identifiers."
    )
    return "\n".join(lines)


@register("rankgpt-listwise")
class RankGPTReranker:
    """Generative listwise baseline. Slides a window when the list is large."""

    requires_training = False

    def __init__(
        self,
        *,
        window_size: int = DEFAULT_WINDOW_SIZE,
        stride: int = DEFAULT_STRIDE,
        max_tokens: int = DEFAULT_MAX_TOKENS,
        client: LLMClient | None = None,
        model: str | None = None,
        api_key: str | None = None,
    ):
        if window_size <= 0 or stride <= 0 or stride > window_size:
            raise ValueError("require 0 < stride <= window_size")
        self.window_size = window_size
        self.stride = stride
        self.max_tokens = max_tokens
        self._client = client
        self._model = model
        self._api_key = api_key

    @property
    def client(self) -> LLMClient:
        if self._client is None:
            self._client = AnthropicLLMClient(model=self._model, api_key=self._api_key)
        return self._client

    def rerank(
        self,
        source: MarketDoc,
        candidates: Sequence[MarketDoc],
        *,
        cutoff: TemporalCutoff,
    ) -> list[ScoredCandidate]:
        _gate_candidates(candidates, cutoff)
        if not candidates:
            return []
        ordered_indices = self._slide_and_rank(source, list(range(len(candidates))), candidates)
        # Synthetic scores: rank 1 gets the highest score; gives the benchmark a
        # monotonic mapping for NDCG/MRR while staying honest about what we have
        # (a permutation, not real relevance probabilities).
        n = len(ordered_indices)
        return [
            ScoredCandidate(
                market_id=candidates[idx].market_id,
                score=float(n - rank),  # rank 0 -> highest score
                rank=rank + 1,
            )
            for rank, idx in enumerate(ordered_indices)
        ]

    # ----- sliding window ------------------------------------------------

    def _slide_and_rank(
        self,
        source: MarketDoc,
        order: list[int],
        candidates: Sequence[MarketDoc],
    ) -> list[int]:
        """Apply the RankGPT sliding-window protocol.

        For `len(order) <= window_size`: one LLM call. Otherwise: slide a
        window of size `window_size` from the bottom of the list to the top,
        each call reranking the contents of the window. After each call, the
        top `stride` items of that window are "locked in" at the top of the
        window range; the next window starts `stride` positions higher.

        This is the standard RankGPT protocol: it propagates strong candidates
        upward in O(N/stride) LLM calls.
        """
        n = len(order)
        if n <= self.window_size:
            return self._rank_window(source, order, candidates)

        end = n
        while end > 0:
            start = max(0, end - self.window_size)
            window_indices = order[start:end]
            ranked_window = self._rank_window(source, window_indices, candidates)
            order[start:end] = ranked_window
            if start == 0:
                break
            end -= self.stride
        return order

    def _rank_window(
        self,
        source: MarketDoc,
        window: list[int],
        candidates: Sequence[MarketDoc],
    ) -> list[int]:
        """One LLM call over a window. Returns `window` reordered."""
        window_docs = [candidates[i] for i in window]
        prompt = _build_user_prompt(source, window_docs)
        try:
            raw = self.client.complete(
                system=SYSTEM_PROMPT, user=prompt, max_tokens=self.max_tokens
            )
        except Exception as exc:
            logger.warning("RankGPT LLM call failed: %s; keeping original order", exc)
            return list(window)
        permutation = parse_permutation(raw, expected_count=len(window))
        return [window[p] for p in permutation]
