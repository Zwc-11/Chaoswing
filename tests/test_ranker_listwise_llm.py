"""Tests for the RankGPT-style listwise LLM reranker.

The LLM client is mocked everywhere — these tests do not call the network
and do not require an API key. We cover:

  * leakage gate (post-cutoff candidate refused before any LLM call)
  * permutation parser robustness (canonical, partial, malformed, hallucinated ids)
  * sliding window protocol (correct number of calls, correct windows)
  * end-to-end rerank with a deterministic mock client
  * AnthropicLLMClient.api_key resolution + missing-key error path
"""
from __future__ import annotations

import unittest
from datetime import datetime, timedelta, timezone

from chaoswing.ml._types import MarketDoc, TemporalCutoff
from chaoswing.ml.leakage import LeakageError

from apps.ranker.services.listwise_llm import (
    AnthropicLLMClient,
    RankGPTReranker,
    parse_permutation,
)


CUTOFF = TemporalCutoff(
    timestamp=datetime(2025, 6, 1, tzinfo=timezone.utc),
    label="bench_cutoff",
)
PRE = CUTOFF.timestamp - timedelta(days=30)
POST = CUTOFF.timestamp + timedelta(days=1)


def _doc(market_id: str, title: str, *, first_seen: datetime = PRE) -> MarketDoc:
    return MarketDoc(market_id=market_id, title=title, first_seen=first_seen, event_family=market_id)


class _ScriptedClient:
    """LLMClient stub returning a queue of pre-baked responses."""

    def __init__(self, responses: list[str]):
        self._responses = list(responses)
        self.calls: list[dict] = []

    def complete(self, *, system: str, user: str, max_tokens: int) -> str:
        self.calls.append({"system": system, "user": user, "max_tokens": max_tokens})
        if not self._responses:
            raise AssertionError("ScriptedClient ran out of responses")
        return self._responses.pop(0)


# ---------------------------------------------------------------------------
# Leakage gate
# ---------------------------------------------------------------------------


class RankGPTLeakageTests(unittest.TestCase):
    def test_post_cutoff_candidate_rejected_before_llm_call(self) -> None:
        client = _ScriptedClient([])  # will error if called
        ranker = RankGPTReranker(client=client)
        source = _doc("source", "Federal Reserve interest rates")
        candidates = [
            _doc("a", "Federal Reserve hikes rates"),
            _doc("future", "Future market", first_seen=POST),
        ]
        with self.assertRaises(LeakageError):
            ranker.rerank(source, candidates, cutoff=CUTOFF)
        self.assertEqual(client.calls, [])  # gate ran first

    def test_empty_candidates_no_llm_call(self) -> None:
        client = _ScriptedClient([])
        ranker = RankGPTReranker(client=client)
        result = ranker.rerank(_doc("s", "anything"), [], cutoff=CUTOFF)
        self.assertEqual(result, [])
        self.assertEqual(client.calls, [])


# ---------------------------------------------------------------------------
# Permutation parser
# ---------------------------------------------------------------------------


class PermutationParserTests(unittest.TestCase):
    def test_canonical_format(self) -> None:
        self.assertEqual(parse_permutation("[3] > [1] > [4] > [2]", expected_count=4), [2, 0, 3, 1])

    def test_handles_extra_whitespace_and_prose(self) -> None:
        raw = "Sure, here is the ranking: [2] > [4] > [1] > [3]. Hope this helps!"
        self.assertEqual(parse_permutation(raw, expected_count=4), [1, 3, 0, 2])

    def test_duplicates_ignored_after_first(self) -> None:
        self.assertEqual(parse_permutation("[1] > [2] > [1] > [3]", expected_count=3), [0, 1, 2])

    def test_out_of_range_dropped(self) -> None:
        # `[9]` is hallucinated for a list of size 3.
        self.assertEqual(parse_permutation("[2] > [9] > [3] > [1]", expected_count=3), [1, 2, 0])

    def test_partial_response_fills_missing_with_identity(self) -> None:
        """Only `[3]` parsed cleanly; the rest fall through in original order."""
        self.assertEqual(parse_permutation("ignore me [3]", expected_count=4), [2, 0, 1, 3])

    def test_total_garbage_returns_identity(self) -> None:
        self.assertEqual(parse_permutation("no brackets here", expected_count=3), [0, 1, 2])


# ---------------------------------------------------------------------------
# Sliding window
# ---------------------------------------------------------------------------


class SlidingWindowTests(unittest.TestCase):
    def test_single_call_when_within_window(self) -> None:
        client = _ScriptedClient(["[3] > [1] > [4] > [2]"])
        ranker = RankGPTReranker(client=client, window_size=10, stride=5)
        candidates = [_doc(f"c{i}", f"Candidate {i}") for i in range(4)]
        result = ranker.rerank(_doc("s", "query"), candidates, cutoff=CUTOFF)
        self.assertEqual(len(client.calls), 1)
        self.assertEqual([r.market_id for r in result], ["c2", "c0", "c3", "c1"])
        # Scores monotonically decreasing
        self.assertGreater(result[0].score, result[-1].score)

    def test_multi_window_slides_bottom_up(self) -> None:
        """20 candidates, window=10, stride=5 => 3 LLM calls.

        Windows visited (start..end):
          [10..20]  (bottom window)
          [5..15]
          [0..10]   (top — terminates since start==0)
        """
        client = _ScriptedClient(["[1]"] * 3)  # any response; we count calls
        ranker = RankGPTReranker(client=client, window_size=10, stride=5)
        candidates = [_doc(f"c{i}", f"Candidate {i}") for i in range(20)]
        ranker.rerank(_doc("s", "query"), candidates, cutoff=CUTOFF)
        self.assertEqual(len(client.calls), 3)
        # Each window should contain exactly 10 numbered items.
        for call in client.calls:
            self.assertEqual(call["user"].count("\n["), 10)

    def test_permutation_propagates_top_candidate_upward(self) -> None:
        """If every window picks `[1]` first, the bottom candidate climbs.

        With 20 candidates, window=10, stride=5, and the LLM always saying
        "the last passage in the window is best," the bottom candidate (c19)
        should end up #1 overall. (It gets selected in window [10..20],
        included in window [5..15], selected again, then again at [0..10].)
        """
        # Each response says "[10] > [9] > ... > [1]" reversing the window.
        reverse_response = " > ".join(f"[{i}]" for i in range(10, 0, -1))
        client = _ScriptedClient([reverse_response] * 5)  # generous
        ranker = RankGPTReranker(client=client, window_size=10, stride=5)
        candidates = [_doc(f"c{i}", f"Candidate {i}") for i in range(20)]
        result = ranker.rerank(_doc("s", "query"), candidates, cutoff=CUTOFF)
        # The bottom candidate after reverse-bubble-up should be at the top.
        self.assertEqual(result[0].market_id, "c19")

    def test_window_must_be_positive(self) -> None:
        with self.assertRaises(ValueError):
            RankGPTReranker(window_size=0, stride=1)

    def test_stride_must_not_exceed_window(self) -> None:
        with self.assertRaises(ValueError):
            RankGPTReranker(window_size=5, stride=10)


# ---------------------------------------------------------------------------
# LLM client error paths (no network — just the resolver)
# ---------------------------------------------------------------------------


class AnthropicLLMClientTests(unittest.TestCase):
    def test_missing_api_key_raises_runtime_error(self) -> None:
        import os
        from django.test.utils import override_settings

        prior = os.environ.pop("ANTHROPIC_API_KEY", None)
        try:
            with override_settings(CHAOSWING_ANTHROPIC_API_KEY=""):
                client = AnthropicLLMClient(api_key=None)
                with self.assertRaises(RuntimeError) as ctx:
                    client._get_client()
                self.assertIn("Anthropic API key not configured", str(ctx.exception))
        finally:
            if prior is not None:
                os.environ["ANTHROPIC_API_KEY"] = prior

    def test_explicit_api_key_wins(self) -> None:
        client = AnthropicLLMClient(api_key="explicit")
        self.assertEqual(client._resolve_api_key(), "explicit")


# ---------------------------------------------------------------------------
# Failure mode: an LLM call that errors keeps the window's original order
# ---------------------------------------------------------------------------


class _BoomClient:
    def complete(self, *, system: str, user: str, max_tokens: int) -> str:
        raise RuntimeError("simulated API failure")


class RankGPTGracefulDegradationTests(unittest.TestCase):
    def test_llm_error_falls_back_to_identity(self) -> None:
        ranker = RankGPTReranker(client=_BoomClient())
        candidates = [_doc(f"c{i}", f"Candidate {i}") for i in range(3)]
        result = ranker.rerank(_doc("s", "query"), candidates, cutoff=CUTOFF)
        self.assertEqual([r.market_id for r in result], ["c0", "c1", "c2"])
