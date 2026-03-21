from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any

from .contracts import PolymarketEventSnapshot, RelatedEventCandidate


@dataclass(slots=True)
class PipelineStageRecord:
    stage: str
    status: str
    detail: str
    metadata: dict[str, Any] = field(default_factory=dict)
    latency_ms: int = 0
    token_input: int = 0
    token_output: int = 0
    cost_usd: float = 0.0
    citations: list[str] = field(default_factory=list)

    def as_dict(self) -> dict[str, Any]:
        return {
            "stage": self.stage,
            "status": self.status,
            "detail": self.detail,
            "metadata": self.metadata,
            "latency_ms": self.latency_ms,
            "token_input": self.token_input,
            "token_output": self.token_output,
            "cost_usd": self.cost_usd,
            "citations": self.citations,
        }


class GraphAgentPipeline:
    conceptual_types = {"Entity", "Evidence", "Rule", "Hypothesis"}

    def plan(
        self,
        snapshot: PolymarketEventSnapshot,
        seed_payload: dict[str, Any],
        related_candidates: list[RelatedEventCandidate],
    ) -> PipelineStageRecord:
        graph = seed_payload.get("graph", {})
        node_count = len(graph.get("nodes") or [])
        edge_count = len(graph.get("edges") or [])
        themes = [tag for tag in snapshot.tags[:3] if tag]
        next_titles = [
            candidate.snapshot.title
            for candidate in related_candidates[:3]
            if candidate.snapshot.title
        ]
        detail = (
            f"Planned the graph pass around {snapshot.title}, starting from "
            f"{node_count} nodes and {edge_count} edges."
        )
        if next_titles:
            detail += f" Candidate follow-on markets: {', '.join(next_titles)}."
        return PipelineStageRecord(
            stage="planner",
            status="completed",
            detail=detail,
            citations=[snapshot.canonical_url],
            metadata={
                "event_slug": snapshot.slug,
                "seed_node_count": node_count,
                "seed_edge_count": edge_count,
                "theme_count": len(themes),
                "candidate_count": len(related_candidates),
                "themes": themes,
            },
        )

    def retrieve(
        self,
        snapshot: PolymarketEventSnapshot,
        related_candidates: list[RelatedEventCandidate],
    ) -> PipelineStageRecord:
        citations = [snapshot.canonical_url]
        seen = {snapshot.canonical_url}
        related_titles = []
        for candidate in related_candidates:
            related_titles.append(candidate.snapshot.title)
            url = candidate.snapshot.canonical_url
            if url and url not in seen:
                citations.append(url)
                seen.add(url)
        status = "completed" if len(citations) > 1 else "fallback"
        detail = (
            f"Retrieved {len(citations)} source markets for grounding."
            if len(citations) > 1
            else "Only the source market was available for grounding; no adjacent source URLs were added."
        )
        return PipelineStageRecord(
            stage="retriever",
            status=status,
            detail=detail,
            citations=citations,
            metadata={
                "source_market_count": len(citations),
                "related_candidate_count": len(related_candidates),
                "related_titles": related_titles[:5],
            },
        )

    def graph_editor(
        self,
        *,
        expansion: dict[str, Any] | None,
        llm_trace: dict[str, Any] | None,
    ) -> PipelineStageRecord:
        if not expansion:
            return PipelineStageRecord(
                stage="graph_editor",
                status="skipped",
                detail="No LLM graph expansion was applied.",
            )
        citations = self._citations_from_expansion(expansion)
        return PipelineStageRecord(
            stage="graph_editor",
            status="completed",
            detail=(
                f"Applied graph edits with {len(expansion.get('node_additions', []))} node additions, "
                f"{len(expansion.get('edge_additions', []))} edge additions, "
                f"{len(expansion.get('node_updates', []))} node updates, and "
                f"{len(expansion.get('edge_updates', []))} edge updates."
            ),
            citations=citations,
            metadata={
                "node_additions": len(expansion.get("node_additions", [])),
                "edge_additions": len(expansion.get("edge_additions", [])),
                "node_updates": len(expansion.get("node_updates", [])),
                "edge_updates": len(expansion.get("edge_updates", [])),
                **self._llm_metadata(llm_trace),
            },
            latency_ms=int((llm_trace or {}).get("latency_ms", 0) or 0),
            token_input=int((llm_trace or {}).get("token_input", 0) or 0),
            token_output=int((llm_trace or {}).get("token_output", 0) or 0),
            cost_usd=float((llm_trace or {}).get("cost_usd", 0.0) or 0.0),
        )

    def verify(self, payload: dict[str, Any]) -> PipelineStageRecord:
        nodes = payload.get("graph", {}).get("nodes") or []
        edges = payload.get("graph", {}).get("edges") or []
        unsupported_nodes = 0
        source_backed_nodes = 0
        evidence_backed_nodes = 0
        citations = []
        for node in nodes:
            node_type = str(node.get("type") or "")
            source_url = str(node.get("source_url") or "").strip()
            evidence = node.get("evidence_snippets") or []
            metadata = node.get("metadata") or []
            if source_url:
                source_backed_nodes += 1
                if source_url not in citations:
                    citations.append(source_url)
            if evidence:
                evidence_backed_nodes += 1
            if node_type in self.conceptual_types and not source_url and not evidence and not metadata:
                unsupported_nodes += 1
        missing_edge_explanations = sum(1 for edge in edges if not str(edge.get("explanation") or "").strip())
        status = "completed" if unsupported_nodes == 0 and missing_edge_explanations == 0 else "fallback"
        detail = (
            f"Verified {len(nodes)} nodes and {len(edges)} edges with "
            f"{source_backed_nodes} source-backed nodes and {evidence_backed_nodes} evidence-backed nodes."
        )
        if unsupported_nodes or missing_edge_explanations:
            detail += (
                f" Found {unsupported_nodes} unsupported conceptual nodes and "
                f"{missing_edge_explanations} edges without explanations."
            )
        return PipelineStageRecord(
            stage="verifier",
            status=status,
            detail=detail,
            citations=citations[:8],
            metadata={
                "node_count": len(nodes),
                "edge_count": len(edges),
                "source_backed_nodes": source_backed_nodes,
                "evidence_backed_nodes": evidence_backed_nodes,
                "unsupported_nodes": unsupported_nodes,
                "missing_edge_explanations": missing_edge_explanations,
            },
        )

    def critic(
        self,
        review: dict[str, Any] | None,
        *,
        llm_trace: dict[str, Any] | None = None,
    ) -> PipelineStageRecord:
        if not review:
            return PipelineStageRecord(
                stage="critic",
                status="skipped",
                detail="No review pass was available for the final graph.",
            )
        issues = review.get("issues") or []
        actions = review.get("follow_up_actions") or []
        approved = bool(review.get("approved"))
        return PipelineStageRecord(
            stage="critic",
            status="completed" if approved else "fallback",
            detail=(
                f"Critic review {'approved' if approved else 'flagged'} the graph with "
                f"{len(issues)} issues and {len(actions)} follow-up actions."
            ),
            metadata={
                "approved": approved,
                "issue_count": len(issues),
                "follow_up_count": len(actions),
                "quality_score": review.get("quality_score", 0.0),
                **self._llm_metadata(llm_trace),
            },
            latency_ms=int((llm_trace or {}).get("latency_ms", 0) or 0),
            token_input=int((llm_trace or {}).get("token_input", 0) or 0),
            token_output=int((llm_trace or {}).get("token_output", 0) or 0),
            cost_usd=float((llm_trace or {}).get("cost_usd", 0.0) or 0.0),
        )

    def _citations_from_expansion(self, expansion: dict[str, Any]) -> list[str]:
        citations: list[str] = []
        for collection_key in ("node_additions", "node_updates"):
            for item in expansion.get(collection_key, []):
                if not isinstance(item, dict):
                    continue
                url = str(item.get("source_url") or "").strip()
                if url and url not in citations:
                    citations.append(url)
        return citations

    def _llm_metadata(self, llm_trace: dict[str, Any] | None) -> dict[str, Any]:
        trace = llm_trace or {}
        metadata = {
            "provider": str(trace.get("provider") or "").strip(),
            "model": str(trace.get("model") or "").strip(),
            "response_id": str(trace.get("response_id") or "").strip(),
            "stop_reason": str(trace.get("stop_reason") or "").strip(),
        }
        return {key: value for key, value in metadata.items() if value}
