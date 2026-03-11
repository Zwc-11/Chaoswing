from __future__ import annotations

import json
import re
from typing import TYPE_CHECKING, Any

from django.conf import settings

if TYPE_CHECKING:
    from anthropic.types import TextBlock


JSON_BLOCK_RE = re.compile(r"\{.*\}", re.DOTALL)


class AnthropicGraphAgent:
    """Optional Anthropic-backed pass for graph expansion and review."""

    def __init__(
        self,
        api_key: str | None = None,
        model: str | None = None,
        enabled: bool | None = None,
    ):
        self.api_key = api_key or settings.CHAOSWING_ANTHROPIC_API_KEY
        self.model = model or settings.CHAOSWING_ANTHROPIC_MODEL
        self.enabled = settings.CHAOSWING_ENABLE_LLM if enabled is None else enabled

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

        prompt = {
            "task": "Expand a prediction-market butterfly graph with a small number of high-value causal nodes and edge refinements.",
            "snapshot": snapshot,
            "graph_seed": seed_payload,
            "instructions": [
                "Treat this like an analyst workflow, not a chat response.",
                "Preserve existing node ids and edge ids.",
                "You may add at most 4 new nodes and 6 new edges.",
                "Only add nodes of type Entity, Evidence, or Hypothesis.",
                "Every new node must include a concise summary, confidence, metadata, and a source_url.",
                "Every new edge must connect valid existing or added nodes.",
                "Return strict JSON only.",
            ],
            "response_schema": {
                "event_description": "string",
                "node_updates": [
                    {"id": "string", "summary": "string", "source_url": "string"}
                ],
                "edge_updates": [{"id": "string", "explanation": "string"}],
                "node_additions": [
                    {
                        "id": "string",
                        "label": "string",
                        "type": "Entity | Evidence | Hypothesis",
                        "confidence": 0.74,
                        "summary": "string",
                        "source_url": "string",
                        "metadata": [{"label": "string", "value": "string"}],
                        "evidence_snippets": ["string"],
                    }
                ],
                "edge_additions": [
                    {
                        "id": "string",
                        "source": "string",
                        "target": "string",
                        "type": "mentions | involves | supported_by | related_to | affects_directly | affects_indirectly | governed_by_rule",
                        "confidence": 0.71,
                        "explanation": "string",
                    }
                ],
                "workflow_notes": ["string"],
            },
        }

        content = self._call_model(prompt, max_tokens=2200)
        if not content:
            return None
        return self._parse_json(content)

    def review_graph(self, snapshot: dict[str, Any], payload: dict[str, Any]) -> dict[str, Any] | None:
        if not self.available:
            return None

        prompt = {
            "task": "Review a prediction-market butterfly graph for structural, narrative, and source-link quality.",
            "snapshot": snapshot,
            "graph_payload": payload,
            "instructions": [
                "Return strict JSON only.",
                "Focus on missing sources, weak causal links, overconfident edges, and confusing node summaries.",
                "Do not rewrite the whole graph.",
            ],
            "response_schema": {
                "approved": True,
                "issues": ["string"],
                "follow_up_actions": ["string"],
                "quality_score": 0.82,
            },
        }

        content = self._call_model(prompt, max_tokens=1200)
        if not content:
            return None
        return self._parse_json(content)

    def _call_model(self, prompt: dict[str, Any], max_tokens: int) -> str:
        try:
            from anthropic import Anthropic
        except ImportError:
            return ""

        client = Anthropic(api_key=self.api_key)
        response = client.messages.create(
            model=self.model,
            max_tokens=max_tokens,
            temperature=0.2,
            messages=[
                {
                    "role": "user",
                    "content": json.dumps(prompt, ensure_ascii=True),
                }
            ],
        )

        from anthropic.types import TextBlock as _TextBlock

        return "".join(
            block.text for block in response.content if isinstance(block, _TextBlock)
        )

    def _parse_json(self, content: str) -> dict[str, Any] | None:
        if not content:
            return None

        candidate = content.strip()
        if not candidate.startswith("{"):
            match = JSON_BLOCK_RE.search(candidate)
            if not match:
                return None
            candidate = match.group(0)

        try:
            return json.loads(candidate)
        except json.JSONDecodeError:
            return None
