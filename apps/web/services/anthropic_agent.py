from __future__ import annotations

import json
import logging
import re
import time
from typing import TYPE_CHECKING, Any
from urllib.parse import urlparse

from django.conf import settings

if TYPE_CHECKING:
    from anthropic.types import TextBlock

logger = logging.getLogger("apps.web.services.anthropic_agent")


JSON_BLOCK_RE = re.compile(r"\{.*\}", re.DOTALL)
MODEL_PRICE_TABLE = (
    (re.compile(r"claude-opus-4(?:[-.]\d+)?", re.IGNORECASE), (15.0, 75.0)),
    (re.compile(r"claude-sonnet-(?:4(?:[-.]\d+)?|3-7|3-5)", re.IGNORECASE), (3.0, 15.0)),
    (re.compile(r"claude-haiku-3-5", re.IGNORECASE), (0.8, 4.0)),
    (re.compile(r"claude-haiku-3(?:[-.]\d+)?", re.IGNORECASE), (0.25, 1.25)),
)

SYSTEM_PROMPT = """\
You are ChaosWing's causal analysis engine — a senior prediction-market analyst \
who builds butterfly-effect graphs from Polymarket events.

CORE PRINCIPLES:
1. Every claim must be grounded in verifiable evidence or clearly labeled as a hypothesis.
2. Source URLs must be real, verifiable Polymarket event links. NEVER fabricate URLs. \
   If you cannot find a real URL, use the canonical event URL provided.
3. Confidence scores must reflect genuine epistemic uncertainty — not optimism.
4. Edges represent causal or evidential relationships, not mere co-occurrence.

REASONING APPROACH:
- Think step by step before producing output.
- First identify the core causal mechanism, then trace second and third-order effects.
- Consider counterfactuals: what would change the probability?
- Separate direct evidence from inference.
- Flag uncertainty explicitly rather than hallucinating precision.

OUTPUT:
- Return strict JSON only. No markdown, no prose outside the JSON object.
- All string values must be grounded in the provided data or clearly reasoned from it.
"""

MAX_RETRY_ATTEMPTS = 2
RETRY_DELAY_SECONDS = 5


def _slim_snapshot(snapshot: dict[str, Any]) -> dict[str, Any]:
    """Extract only the fields Claude needs — no images, no raw market arrays."""
    markets_summary = []
    for m in (snapshot.get("markets") or [])[:6]:
        markets_summary.append({
            "question": m.get("question", "")[:120],
            "outcomes": m.get("outcomes", [])[:4],
            "outcome_prices": m.get("outcome_prices", [])[:4],
            "volume": m.get("volume", 0),
        })

    return {
        "title": snapshot.get("title", ""),
        "description": (snapshot.get("description") or "")[:300],
        "canonical_url": snapshot.get("canonical_url", ""),
        "slug": snapshot.get("slug", ""),
        "status": snapshot.get("status", ""),
        "category": snapshot.get("category", ""),
        "tags": (snapshot.get("tags") or [])[:8],
        "outcomes": (snapshot.get("outcomes") or [])[:6],
        "volume": snapshot.get("volume", 0),
        "liquidity": snapshot.get("liquidity", 0),
        "markets_summary": markets_summary,
    }


def _slim_graph(payload: dict[str, Any]) -> dict[str, Any]:
    """Strip images/assets and large metadata — keep only graph structure."""
    nodes = []
    for n in payload.get("graph", {}).get("nodes", []):
        nodes.append({
            "id": n["id"],
            "label": n.get("label", ""),
            "type": n.get("type", ""),
            "confidence": n.get("confidence", 0),
            "summary": (n.get("summary") or "")[:150],
            "source_url": n.get("source_url", ""),
        })

    edges = []
    for e in payload.get("graph", {}).get("edges", []):
        edges.append({
            "id": e["id"],
            "source": e["source"],
            "target": e["target"],
            "type": e.get("type", ""),
            "confidence": e.get("confidence", 0),
            "explanation": (e.get("explanation") or "")[:120],
        })

    return {"nodes": nodes, "edges": edges}


class AnthropicGraphAgent:
    """Anthropic-backed graph expansion and review with chain-of-thought reasoning.

    Sends only minimal data to Claude to stay within rate limits and save tokens.
    """

    def __init__(
        self,
        api_key: str | None = None,
        model: str | None = None,
        enabled: bool | None = None,
    ):
        self.api_key = api_key or settings.CHAOSWING_ANTHROPIC_API_KEY
        self.model = model or settings.CHAOSWING_ANTHROPIC_MODEL
        self.enabled = settings.CHAOSWING_ENABLE_LLM if enabled is None else enabled
        self.input_cost_per_mtok = float(
            getattr(settings, "CHAOSWING_ANTHROPIC_INPUT_COST_PER_MTOK", 0.0) or 0.0
        )
        self.output_cost_per_mtok = float(
            getattr(settings, "CHAOSWING_ANTHROPIC_OUTPUT_COST_PER_MTOK", 0.0) or 0.0
        )

    @property
    def available(self) -> bool:
        return bool(self.enabled and self.api_key)

    def expand_graph(
        self,
        snapshot: dict[str, Any],
        seed_payload: dict[str, Any],
    ) -> dict[str, Any] | None:
        if not self.available:
            return None

        slim = _slim_snapshot(snapshot)
        canonical_url = slim["canonical_url"]
        event_title = slim["title"]
        graph = _slim_graph(seed_payload)
        existing_node_ids = [n["id"] for n in graph["nodes"]]

        prompt = {
            "task": "Expand a prediction-market butterfly graph.",
            "event": slim,
            "graph": graph,
            "existing_node_ids": existing_node_ids,
            "instructions": [
                f"Event: '{event_title}'. Add 2-4 new causal nodes and 3-6 new edges.",
                "Only add nodes of type Entity, Evidence, or Hypothesis.",
                "Preserve all existing node/edge IDs.",
                f"For source_url: use real Polymarket URLs or '{canonical_url}'.",
                "Confidence: 0.90+=near-certain, 0.75-0.89=strong, 0.60-0.74=inference, 0.45-0.59=speculative.",
                "Edge explanations must describe CAUSAL MECHANISMS.",
                "Return strict JSON only.",
            ],
            "response_schema": {
                "reasoning": "brief chain-of-thought",
                "event_description": "string",
                "node_additions": [{"id": "str", "label": "str", "type": "Entity|Evidence|Hypothesis", "confidence": 0.7, "summary": "str", "source_url": "str", "metadata": [{"label": "str", "value": "str"}], "evidence_snippets": ["str"]}],
                "edge_additions": [{"id": "str", "source": "str", "target": "str", "type": "mentions|involves|supported_by|related_to|affects_directly|affects_indirectly|governed_by_rule", "confidence": 0.7, "explanation": "str"}],
                "node_updates": [{"id": "str", "summary": "str"}],
                "edge_updates": [{"id": "str", "explanation": "str"}],
                "workflow_notes": ["str"],
            },
        }

        content, trace = self._call_model(prompt, max_tokens=4000)
        if not content:
            return None
        parsed = self._parse_json(content)
        if parsed:
            parsed = self._sanitize_source_urls(parsed, canonical_url)
            parsed["_llm_trace"] = trace
        return parsed

    def review_graph(self, snapshot: dict[str, Any], payload: dict[str, Any]) -> dict[str, Any] | None:
        if not self.available:
            return None

        slim = _slim_snapshot(snapshot)
        graph = _slim_graph(payload)

        prompt = {
            "task": "Review a prediction-market butterfly graph for quality.",
            "event": slim,
            "graph": graph,
            "instructions": [
                "Check: broken source links, weak causal claims, overconfident edges, missing connections.",
                "Name specific node/edge IDs for each issue.",
                "Score quality 0.55-0.85 range (not 0.90+).",
                "Return strict JSON only.",
            ],
            "response_schema": {
                "reasoning": "brief review",
                "approved": True,
                "issues": ["string descriptions with node/edge IDs"],
                "follow_up_actions": ["str"],
                "quality_score": 0.72,
            },
        }

        content, trace = self._call_model(prompt, max_tokens=1200)
        if not content:
            return None
        parsed = self._parse_json(content)
        if parsed:
            parsed = self._normalize_review(parsed)
            parsed["_llm_trace"] = trace
        return parsed

    def _call_model(self, prompt: dict[str, Any], max_tokens: int) -> tuple[str, dict[str, Any]]:
        try:
            from anthropic import Anthropic
        except ImportError:
            logger.error("anthropic package is not installed")
            return "", {}

        prompt_json = json.dumps(prompt, ensure_ascii=True)
        estimated_tokens = len(prompt_json) // 3
        logger.info("LLM prompt: ~%d estimated input tokens", estimated_tokens)

        client = Anthropic(api_key=self.api_key)

        for attempt in range(1, MAX_RETRY_ATTEMPTS + 1):
            started_at = time.perf_counter()
            try:
                response = client.messages.create(
                    model=self.model,
                    max_tokens=max_tokens,
                    temperature=0.15,
                    system=SYSTEM_PROMPT,
                    messages=[{"role": "user", "content": prompt_json}],
                )
                from anthropic.types import TextBlock as _TextBlock
                content = "".join(
                    block.text for block in response.content if isinstance(block, _TextBlock)
                )
                usage = getattr(response, "usage", None)
                input_tokens = int(
                    getattr(usage, "input_tokens", estimated_tokens) or estimated_tokens
                )
                output_tokens = int(
                    getattr(usage, "output_tokens", 0) or max(len(content) // 4, 0)
                )
                estimated_cost = self._estimate_cost_usd(
                    input_tokens=input_tokens,
                    output_tokens=output_tokens,
                )
                trace = {
                    "provider": "anthropic",
                    "model": self.model,
                    "latency_ms": int((time.perf_counter() - started_at) * 1000),
                    "token_input": input_tokens,
                    "token_output": output_tokens,
                    "cost_usd": estimated_cost,
                    "response_id": str(getattr(response, "id", "") or ""),
                    "stop_reason": str(getattr(response, "stop_reason", "") or ""),
                    "estimated_input_tokens": estimated_tokens,
                }
                return content, trace
            except Exception as exc:
                is_rate_limit = "rate_limit" in str(type(exc).__name__).lower() or "429" in str(exc)
                if is_rate_limit and attempt < MAX_RETRY_ATTEMPTS:
                    wait = RETRY_DELAY_SECONDS * attempt
                    logger.warning("Rate limited (attempt %d/%d), retrying in %ds", attempt, MAX_RETRY_ATTEMPTS, wait)
                    time.sleep(wait)
                    continue
                logger.exception("Anthropic API call failed (attempt %d/%d)", attempt, MAX_RETRY_ATTEMPTS)
                return "", {
                    "provider": "anthropic",
                    "model": self.model,
                    "latency_ms": int((time.perf_counter() - started_at) * 1000),
                    "token_input": estimated_tokens,
                    "token_output": 0,
                    "cost_usd": self._estimate_cost_usd(
                        input_tokens=estimated_tokens,
                        output_tokens=0,
                    ),
                    "error": str(type(exc).__name__),
                }
        return "", {}

    def _estimate_cost_usd(self, *, input_tokens: int, output_tokens: int) -> float:
        input_rate, output_rate = self._pricing_rates()
        if input_rate <= 0 and output_rate <= 0:
            return 0.0
        cost = ((max(input_tokens, 0) / 1_000_000) * input_rate) + (
            (max(output_tokens, 0) / 1_000_000) * output_rate
        )
        return round(cost, 6)

    def _pricing_rates(self) -> tuple[float, float]:
        if self.input_cost_per_mtok > 0 or self.output_cost_per_mtok > 0:
            return self.input_cost_per_mtok, self.output_cost_per_mtok
        model_name = str(self.model or "").strip()
        for pattern, rates in MODEL_PRICE_TABLE:
            if pattern.search(model_name):
                return rates
        return 0.0, 0.0

    def _parse_json(self, content: str) -> dict[str, Any] | None:
        if not content:
            return None

        candidate = content.strip()

        md_match = re.search(r"```(?:json)?\s*\n?(.*?)```", candidate, re.DOTALL)
        if md_match:
            candidate = md_match.group(1).strip()
        elif candidate.startswith("```"):
            candidate = re.sub(r"^```(?:json)?\s*\n?", "", candidate).strip()

        if not candidate.startswith("{"):
            match = JSON_BLOCK_RE.search(candidate)
            if not match:
                logger.warning("No JSON found in LLM response (first 200 chars): %s", candidate[:200])
                return None
            candidate = match.group(0)

        for attempt_label, text in [("raw", candidate), ("trailing-comma-fix", None), ("brace-repair", None)]:
            if attempt_label == "trailing-comma-fix":
                text = re.sub(r",\s*([}\]])", r"\1", candidate)
            elif attempt_label == "brace-repair":
                text = self._repair_truncated_json(candidate)
            if text is None:
                continue
            try:
                return json.loads(text)
            except json.JSONDecodeError:
                continue

        logger.warning("All JSON parse attempts failed (first 300 chars): %s", candidate[:300])
        return None

    @staticmethod
    def _repair_truncated_json(text: str) -> str | None:
        """Try to close a truncated JSON object by balancing braces and brackets."""
        text = re.sub(r",\s*$", "", text.rstrip())
        text = re.sub(r':\s*"[^"]*$', ': ""', text)
        text = re.sub(r",\s*([}\]])", r"\1", text)

        open_braces = text.count("{") - text.count("}")
        open_brackets = text.count("[") - text.count("]")

        if open_braces <= 0 and open_brackets <= 0:
            return None

        if text.endswith(","):
            text = text[:-1]

        last_char = text.rstrip()[-1] if text.rstrip() else ""
        if last_char == '"':
            pass
        elif last_char not in "{}[]\"0123456789":
            text = text.rstrip()
            if not text.endswith('"'):
                text += '"'

        text += "]" * max(0, open_brackets)
        text += "}" * max(0, open_braces)

        return text

    def _sanitize_source_urls(self, expansion: dict[str, Any], canonical_url: str) -> dict[str, Any]:
        for node in expansion.get("node_additions", []):
            if not isinstance(node, dict):
                continue
            url = str(node.get("source_url") or "").strip()
            if not self._is_plausible_url(url):
                node["source_url"] = canonical_url
            elif "polymarket.com" in url and not self._is_valid_polymarket_path(url):
                node["source_url"] = canonical_url

        for update in expansion.get("node_updates", []):
            if not isinstance(update, dict):
                continue
            url = str(update.get("source_url") or "").strip()
            if url and not self._is_plausible_url(url):
                update["source_url"] = canonical_url

        return expansion

    def _is_plausible_url(self, url: str) -> bool:
        try:
            parsed = urlparse(url)
            return bool(parsed.scheme in {"http", "https"} and parsed.netloc and len(parsed.netloc) > 3)
        except Exception:
            return False

    def _is_valid_polymarket_path(self, url: str) -> bool:
        try:
            parsed = urlparse(url)
            path = parsed.path.strip("/")
            parts = path.split("/")
            return len(parts) >= 2 and parts[0] == "event" and len(parts[1]) > 3
        except Exception:
            return False

    def _normalize_review(self, review: dict[str, Any]) -> dict[str, Any]:
        issues_raw = review.get("issues", [])
        if isinstance(issues_raw, list):
            normalized_issues = []
            for issue in issues_raw:
                if isinstance(issue, str):
                    normalized_issues.append(issue)
                elif isinstance(issue, dict):
                    desc = str(issue.get("description") or "").strip()
                    node_id = str(issue.get("node_or_edge_id") or "").strip()
                    severity = str(issue.get("severity") or "medium").strip()
                    if desc:
                        prefix = f"[{severity.upper()}]" if severity else ""
                        ref = f" ({node_id})" if node_id else ""
                        normalized_issues.append(f"{prefix}{ref} {desc}".strip())
            review["issues"] = normalized_issues

        if "quality_score" in review:
            try:
                review["quality_score"] = round(max(0.0, min(float(review["quality_score"]), 1.0)), 2)
            except (TypeError, ValueError):
                review["quality_score"] = 0.65

        return review
