from __future__ import annotations

from copy import deepcopy
from datetime import UTC, datetime
from time import perf_counter
from typing import Any, Protocol, cast
from urllib.parse import urlparse

from django.conf import settings
from django.utils import timezone

from apps.web.models import AgentTrace, GraphRun, MarketSnapshot

from .agent_pipeline import GraphAgentPipeline, PipelineStageRecord
from .anthropic_agent import AnthropicGraphAgent
from .contracts import PolymarketEventSnapshot, RelatedEventCandidate
from .graph_builder import GraphConstructionService
from .icons import build_type_icon, fetch_remote_image_data_uri
from .market_intelligence import BenchmarkSummaryService, LandingStatsService
from .ml_hooks import GraphRunDataCollector
from .polymarket import PolymarketMetadataService, RelatedMarketDiscoveryService
from .resolution_labeling import ResolutionLabelingService


# -- Injectable service protocols ---------------------------------------------


class _MetadataServiceProtocol(Protocol):
    def hydrate(self, source_url: str) -> PolymarketEventSnapshot: ...


class _DiscoveryServiceProtocol(Protocol):
    def discover(
        self, snapshot: PolymarketEventSnapshot, limit: int = ...
    ) -> list[RelatedEventCandidate]: ...


class _AgentProtocol(Protocol):
    available: bool
    model: str

    def expand_graph(
        self, snapshot: dict[str, Any], seed_payload: dict[str, Any]
    ) -> dict[str, Any] | None: ...

    def review_graph(
        self, snapshot: dict[str, Any], payload: dict[str, Any]
    ) -> dict[str, Any] | None: ...


# -----------------------------------------------------------------------------

ALLOWED_NODE_TYPES = {"Event", "Entity", "RelatedMarket", "Evidence", "Rule", "Hypothesis"}
ALLOWED_EDGE_TYPES = {
    "mentions",
    "involves",
    "supported_by",
    "related_to",
    "affects_directly",
    "affects_indirectly",
    "governed_by_rule",
}


class GraphWorkflowService:
    """Coordinates event resolution, graph construction, optional review, and persistence."""

    def __init__(
        self,
        metadata_service: _MetadataServiceProtocol | None = None,
        discovery_service: _DiscoveryServiceProtocol | None = None,
        graph_builder: GraphConstructionService | None = None,
        agent: _AgentProtocol | None = None,
    ):
        self.metadata_service = metadata_service or PolymarketMetadataService()
        # cast: RelatedMarketDiscoveryService expects the concrete type; when a custom
        # protocol implementation is injected a discovery_service is always provided too,
        # so this fallback branch is only exercised with a real PolymarketMetadataService.
        self.discovery_service = discovery_service or RelatedMarketDiscoveryService(
            metadata_service=cast(PolymarketMetadataService | None, metadata_service)
        )
        self.graph_builder = graph_builder or GraphConstructionService()
        self.agent = agent or AnthropicGraphAgent()
        self.agent_pipeline = GraphAgentPipeline()
        self.data_collector = GraphRunDataCollector()
        self.resolution_labeling = ResolutionLabelingService()

    def run(self, source_url: str) -> dict[str, Any]:
        workflow_log: list[dict[str, str]] = []
        source_cache: dict[str, Any] = {}
        trace_overrides: dict[str, dict[str, Any]] = {}
        pipeline_traces: list[PipelineStageRecord] = []

        event_resolution_started = perf_counter()
        snapshot = self.metadata_service.hydrate(source_url)
        source_cache[snapshot.canonical_url] = snapshot
        trace_overrides["event_resolution"] = self._trace_override(
            latency_ms=self._elapsed_ms(event_resolution_started),
            citations=[snapshot.canonical_url],
            metadata={
                "event_slug": snapshot.slug,
                "event_status": snapshot.status,
                "source_kind": snapshot.source_kind,
            },
        )
        workflow_log.append(
            {
                "step": "event_resolution",
                "status": "completed",
                "detail": f"Resolved source event from {snapshot.source_kind}.",
            }
        )

        discovery_started = perf_counter()
        related_candidates = self.discovery_service.discover(snapshot, limit=4)
        trace_overrides["related_market_discovery"] = self._trace_override(
            latency_ms=self._elapsed_ms(discovery_started),
            citations=[
                candidate.snapshot.canonical_url
                for candidate in related_candidates
                if candidate.snapshot.canonical_url
            ],
            metadata={
                "candidate_count": len(related_candidates),
                "shared_tags": sorted(
                    {
                        tag
                        for candidate in related_candidates
                        for tag in candidate.shared_tags
                        if tag
                    }
                )[:8],
            },
        )
        workflow_log.append(
            {
                "step": "related_market_discovery",
                "status": "completed" if related_candidates else "fallback",
                "detail": (
                    f"Discovered {len(related_candidates)} related Polymarket events."
                    if related_candidates
                    else "No adjacent live contracts were discovered; the builder will use the event bundle directly."
                ),
            }
        )

        graph_construction_started = perf_counter()
        payload = deepcopy(self.graph_builder.build(snapshot, related_candidates))
        payload["event"]["title"] = snapshot.title
        payload["event"]["source_url"] = snapshot.canonical_url
        payload["event"]["description"] = snapshot.description
        payload["event"]["image_url"] = snapshot.image_url
        payload["event"]["status"] = snapshot.status
        payload["event"]["tags"] = snapshot.tags or payload["event"].get("tags", [])
        payload["event"]["outcomes"] = snapshot.outcomes or payload["event"].get("outcomes", [])
        payload["event"]["updated_at"] = snapshot.updated_at or payload["event"].get("updated_at")
        payload["run"]["generated_at"] = self._iso_now()
        payload["run"]["persistence"] = "database"
        payload["run"]["workflow"] = workflow_log
        payload["run"]["mode"] = (
            "resolved-backend" if snapshot.source_kind != "fallback" else "deterministic-fallback"
        )
        payload["run"]["model_name"] = ""
        payload["context"] = {
            "source_snapshot": snapshot.as_dict(),
            "related_candidates": [candidate.as_dict() for candidate in related_candidates],
        }
        trace_overrides["graph_construction"] = self._trace_override(
            latency_ms=self._elapsed_ms(graph_construction_started),
            citations=[snapshot.canonical_url],
            metadata={
                "node_count": len(payload.get("graph", {}).get("nodes") or []),
                "edge_count": len(payload.get("graph", {}).get("edges") or []),
                "related_candidate_count": len(related_candidates),
            },
        )
        planner_started = perf_counter()
        pipeline_traces.append(
            self._finalize_pipeline_trace(
                self.agent_pipeline.plan(snapshot, payload, related_candidates),
                started_at=planner_started,
                metadata={"execution_mode": "deterministic"},
            )
        )
        retriever_started = perf_counter()
        pipeline_traces.append(
            self._finalize_pipeline_trace(
                self.agent_pipeline.retrieve(snapshot, related_candidates),
                started_at=retriever_started,
                metadata={"execution_mode": "deterministic"},
            )
        )

        workflow_log.append(
            {
                "step": "graph_construction",
                "status": "completed",
                "detail": f"Constructed graph with {len(payload['graph']['nodes'])} nodes before agent expansion.",
            }
        )

        self._attach_node_metadata(payload, snapshot, source_cache)
        payload["assets"] = self._build_assets(payload, snapshot, source_cache, workflow_log)

        if self.agent.available:
            expansion_started = perf_counter()
            expansion = self.agent.expand_graph(snapshot.as_dict(), payload)
            expansion_latency_ms = self._elapsed_ms(expansion_started)
            expansion_trace: dict[str, Any] = {}
            if expansion:
                expansion_trace = self._pop_llm_trace(expansion)
                if expansion_trace:
                    trace_overrides["llm_expansion"] = self._trace_override_from_llm(
                        expansion_trace,
                        citations=self._trace_citations_from_expansion(expansion),
                        metadata={
                            "stage_latency_ms": expansion_latency_ms,
                            "node_additions": len(expansion.get("node_additions", [])),
                            "edge_additions": len(expansion.get("edge_additions", [])),
                        },
                    )
                else:
                    trace_overrides["llm_expansion"] = self._trace_override(
                        latency_ms=expansion_latency_ms,
                        citations=self._trace_citations_from_expansion(expansion),
                        metadata={
                            "model": self.agent.model,
                            "provider": "anthropic",
                            "node_additions": len(expansion.get("node_additions", [])),
                            "edge_additions": len(expansion.get("edge_additions", [])),
                        },
                    )
                self._apply_expansion(payload, expansion, snapshot)
                self._attach_node_metadata(payload, snapshot, source_cache)
                payload["assets"] = self._build_assets(
                    payload,
                    snapshot,
                    source_cache,
                    workflow_log,
                    log_primary_icon=False,
                )
                payload["run"]["mode"] = "agent-enriched"
                payload["run"]["model_name"] = self.agent.model
                payload["run"]["agent_notes"] = expansion.get("workflow_notes", [])
                workflow_log.append(
                    {
                        "step": "llm_expansion",
                        "status": "completed",
                        "detail": (
                            f"Expanded graph with {self.agent.model}; "
                            f"added {len(expansion.get('node_additions', []))} nodes and "
                            f"{len(expansion.get('edge_additions', []))} edges."
                        ),
                    }
                )
                pipeline_traces.append(
                    self._finalize_pipeline_trace(
                        self.agent_pipeline.graph_editor(
                            expansion=expansion,
                            llm_trace=expansion_trace,
                        ),
                        started_at=expansion_started,
                        metadata={
                            "execution_mode": "anthropic",
                            "stage_latency_ms": expansion_latency_ms,
                        },
                    )
                )
            else:
                trace_overrides["llm_expansion"] = self._trace_override(
                    latency_ms=expansion_latency_ms,
                    citations=[snapshot.canonical_url],
                    metadata={"model": self.agent.model, "provider": "anthropic"},
                )
                workflow_log.append(
                    {
                        "step": "llm_expansion",
                        "status": "failed",
                        "detail": "LLM expansion failed; returning deterministic backend graph.",
                    }
                )
                pipeline_traces.append(
                    self._finalize_pipeline_trace(
                        PipelineStageRecord(
                            stage="graph_editor",
                            status="failed",
                            detail="LLM graph expansion failed, so no model-authored edits were applied.",
                            metadata={"execution_mode": "anthropic"},
                        ),
                        started_at=expansion_started,
                        citations=[snapshot.canonical_url],
                    )
                )

            review_started = perf_counter()
            review = self.agent.review_graph(snapshot.as_dict(), payload)
            review_latency_ms = self._elapsed_ms(review_started)
            review_trace: dict[str, Any] = {}
            if review:
                review_trace = self._pop_llm_trace(review)
                if review_trace:
                    trace_overrides["llm_review"] = self._trace_override_from_llm(
                        review_trace,
                        metadata={
                            "stage_latency_ms": review_latency_ms,
                            "approved": bool(review.get("approved")),
                            "issue_count": len(review.get("issues", [])),
                        },
                    )
                else:
                    trace_overrides["llm_review"] = self._trace_override(
                        latency_ms=review_latency_ms,
                        citations=[snapshot.canonical_url],
                        metadata={
                            "model": self.agent.model,
                            "provider": "anthropic",
                            "approved": bool(review.get("approved")),
                            "issue_count": len(review.get("issues", [])),
                        },
                    )
                payload["run"]["review"] = review
                workflow_log.append(
                    {
                        "step": "llm_review",
                        "status": "completed",
                        "detail": "Ran a review pass over the final graph payload.",
                    }
                )
                pipeline_traces.append(
                    self._finalize_pipeline_trace(
                        self.agent_pipeline.critic(review, llm_trace=review_trace),
                        started_at=review_started,
                        metadata={
                            "execution_mode": "anthropic",
                            "stage_latency_ms": review_latency_ms,
                        },
                        citations=[snapshot.canonical_url],
                    )
                )
            else:
                payload["run"]["review"] = self._deterministic_review(payload)
                trace_overrides["llm_review"] = self._trace_override(
                    latency_ms=review_latency_ms,
                    citations=[snapshot.canonical_url],
                    metadata={"model": self.agent.model, "provider": "anthropic"},
                )
                workflow_log.append(
                    {
                        "step": "llm_review",
                        "status": "fallback",
                        "detail": "Agent review was unavailable; stored deterministic validation review instead.",
                    }
                )
                pipeline_traces.append(
                    self._finalize_pipeline_trace(
                        self.agent_pipeline.critic(payload["run"]["review"]),
                        started_at=review_started,
                        metadata={"execution_mode": "deterministic-fallback"},
                        citations=[snapshot.canonical_url],
                    )
                )
        else:
            graph_editor_disabled_started = perf_counter()
            trace_overrides["llm_expansion"] = self._trace_override(
                latency_ms=0,
                citations=[snapshot.canonical_url],
                metadata={"execution_mode": "disabled"},
            )
            trace_overrides["llm_review"] = self._trace_override(
                latency_ms=0,
                citations=[snapshot.canonical_url],
                metadata={"execution_mode": "disabled"},
            )
            workflow_log.append(
                {
                    "step": "llm_expansion",
                    "status": "skipped",
                    "detail": "Anthropic integration is disabled until the API key is configured.",
                }
            )
            workflow_log.append(
                {
                    "step": "llm_review",
                    "status": "skipped",
                    "detail": "Stored deterministic validation review because the agent is disabled.",
                }
            )
            pipeline_traces.append(
                self._finalize_pipeline_trace(
                    PipelineStageRecord(
                        stage="graph_editor",
                        status="skipped",
                        detail="Anthropic graph editing is disabled, so the saved graph remains deterministic.",
                        metadata={"execution_mode": "disabled"},
                    ),
                    started_at=graph_editor_disabled_started,
                    citations=[snapshot.canonical_url],
                )
            )
            deterministic_review_started = perf_counter()
            payload["run"]["review"] = self._deterministic_review(payload)
            pipeline_traces.append(
                self._finalize_pipeline_trace(
                    self.agent_pipeline.critic(payload["run"]["review"]),
                    started_at=deterministic_review_started,
                    metadata={"execution_mode": "deterministic"},
                    citations=[snapshot.canonical_url],
                )
            )

        payload_validation_started = perf_counter()
        self._validate_payload(payload)
        trace_overrides["payload_validation"] = self._trace_override(
            latency_ms=self._elapsed_ms(payload_validation_started),
            citations=[snapshot.canonical_url],
            metadata={
                "node_count": len(payload.get("graph", {}).get("nodes") or []),
                "edge_count": len(payload.get("graph", {}).get("edges") or []),
            },
        )
        workflow_log.append(
            {
                "step": "payload_validation",
                "status": "completed",
                "detail": "Validated graph structure, sources, and icon assets.",
            }
        )
        verifier_started = perf_counter()
        pipeline_traces.append(
            self._finalize_pipeline_trace(
                self.agent_pipeline.verify(payload),
                started_at=verifier_started,
                metadata={"execution_mode": "deterministic"},
            )
        )

        graph_stats = self._graph_stats(payload)
        run = GraphRun(
            source_url=snapshot.canonical_url,
            event_title=payload["event"]["title"],
            event_slug=snapshot.slug,
            status="completed",
            mode=payload["run"]["mode"],
            model_name=payload["run"].get("model_name", ""),
            source_snapshot=snapshot.as_dict(),
            graph_stats=graph_stats,
            payload={},
            workflow_log=workflow_log,
        )

        payload["run"]["id"] = str(run.id)
        payload["run"]["workflow"] = workflow_log
        payload["run"]["graph_stats"] = graph_stats
        payload["run"]["agent_pipeline"] = [record.as_dict() for record in pipeline_traces]
        run.payload = payload
        run.save()
        self._persist_run_artifacts(
            run,
            snapshot,
            payload,
            graph_stats,
            workflow_log,
            trace_overrides=trace_overrides,
            extra_traces=pipeline_traces,
        )

        try:
            self.data_collector.collect(payload)
        except Exception:
            pass
        BenchmarkSummaryService.invalidate_cached_summary()
        LandingStatsService.invalidate_cached_stats()

        return payload

    def review_saved_run(self, run: GraphRun) -> dict[str, Any]:
        payload: dict[str, Any] = deepcopy(cast(dict[str, Any], run.payload))
        source_snapshot: dict[str, Any] = cast(dict[str, Any], run.source_snapshot) or payload.get(
            "context", {}
        ).get("source_snapshot", {})
        if self.agent.available:
            review_started = perf_counter()
            review = self.agent.review_graph(source_snapshot, payload)
            if review:
                review_trace = self._pop_llm_trace(review)
                payload.setdefault("run", {})["review"] = review
                run.payload = payload
                run.updated_at = timezone.now()
                run.save(update_fields=["payload", "updated_at"])
                self._create_agent_trace(
                    run,
                    stage="manual_review",
                    status="completed",
                    detail="Ran a manual review pass against an existing saved run.",
                    metadata={
                        "approved": bool(review.get("approved")),
                        "issue_count": len(review.get("issues", [])),
                        "execution_mode": "anthropic",
                        **self._trace_metadata_from_llm(review_trace),
                    },
                    latency_ms=max(
                        int(review_trace.get("latency_ms", 0) or 0),
                        self._elapsed_ms(review_started),
                    ),
                    token_input=int(review_trace.get("token_input", 0) or 0),
                    token_output=int(review_trace.get("token_output", 0) or 0),
                    cost_usd=float(review_trace.get("cost_usd", 0.0) or 0.0),
                    citations=[str(source_snapshot.get("canonical_url") or source_snapshot.get("source_url") or "")],
                )
                BenchmarkSummaryService.invalidate_cached_summary()
                return review
        deterministic_review_started = perf_counter()
        review = self._deterministic_review(payload)
        payload.setdefault("run", {})["review"] = review
        run.payload = payload
        run.updated_at = timezone.now()
        run.save(update_fields=["payload", "updated_at"])
        self._create_agent_trace(
            run,
            stage="manual_review",
            status="fallback",
            detail="Stored deterministic review output for an existing saved run.",
            metadata={
                "approved": bool(review.get("approved")),
                "issue_count": len(review.get("issues", [])),
                "execution_mode": "deterministic",
            },
            latency_ms=self._elapsed_ms(deterministic_review_started),
            citations=[str(source_snapshot.get("canonical_url") or source_snapshot.get("source_url") or "")],
        )
        BenchmarkSummaryService.invalidate_cached_summary()
        return review

    def _attach_node_metadata(
        self,
        payload: dict[str, Any],
        snapshot: PolymarketEventSnapshot,
        source_cache: dict[str, Any],
    ) -> None:
        nodes = payload["graph"]["nodes"]
        event_description = payload["event"].get("description") or snapshot.description

        for node in nodes:
            if node["type"] in ("RelatedMarket", "Event"):
                node["source_url"] = node.get("source_url") or snapshot.canonical_url
            else:
                node["source_url"] = node.get("source_url") or ""
            source_context = self._get_source_context(
                node.get("source_url") or snapshot.canonical_url, snapshot, source_cache
            )

            node["summary"] = str(node.get("summary") or source_context["description"] or event_description).strip()
            node["description"] = node["summary"]
            node["source_title"] = (
                str(node.get("source_title") or "").strip()
                or source_context["title"]
                or node["label"]
            )
            node["source_description"] = (
                str(node.get("source_description") or "").strip()
                or source_context["description"]
                or node["summary"]
            )
            node["metadata"] = self._clean_metadata(node.get("metadata"))
            node["evidence_snippets"] = self._clean_snippets(node.get("evidence_snippets"))

            if node["type"] == "Event":
                node["source_url"] = snapshot.canonical_url
                node["summary"] = event_description
                node["description"] = event_description
                node["source_title"] = snapshot.title
                node["source_description"] = event_description

    def _build_assets(
        self,
        payload: dict[str, Any],
        snapshot: PolymarketEventSnapshot,
        source_cache: dict[str, Any],
        workflow_log: list[dict[str, str]],
        log_primary_icon: bool = True,
    ) -> dict[str, str]:
        assets: dict[str, str] = {}
        source_asset_keys: dict[str, str] = {}

        event_icon = self._fetch_snapshot_icon(snapshot)
        assets["event_primary"] = event_icon or build_type_icon(snapshot.title, "Event")
        if log_primary_icon:
            workflow_log.append(
                {
                    "step": "event_icon",
                    "status": "completed" if event_icon else "fallback",
                    "detail": (
                        "Fetched and embedded the Polymarket event image."
                        if event_icon
                        else "Using generated fallback icon because no remote event image was available."
                    ),
                }
            )
        source_asset_keys[snapshot.canonical_url] = "event_primary"

        for node in payload["graph"]["nodes"]:
            if node["type"] == "Event":
                node["icon_key"] = "event_primary"
                continue

            source_url = node.get("source_url") or snapshot.canonical_url
            if source_url in source_asset_keys:
                node["icon_key"] = source_asset_keys[source_url]
                continue

            source_context = self._get_source_context(source_url, snapshot, source_cache)
            asset_key = f"source_{self._asset_slug(source_url, node['id'])}"
            icon = ""
            image_url = source_context.get("image_url") or ""
            if image_url and settings.CHAOSWING_ENABLE_REMOTE_FETCH:
                try:
                    icon = fetch_remote_image_data_uri(image_url)
                except Exception:
                    icon = ""

            assets[asset_key] = icon or build_type_icon(node["label"], node["type"])
            source_asset_keys[source_url] = asset_key
            node["icon_key"] = asset_key

        return assets

    def _apply_expansion(
        self,
        payload: dict[str, Any],
        expansion: dict[str, Any],
        snapshot: PolymarketEventSnapshot,
    ) -> None:
        if expansion.get("event_description"):
            payload["event"]["description"] = str(expansion["event_description"]).strip()

        node_lookup = {node["id"]: node for node in payload["graph"]["nodes"]}
        edge_lookup = {edge["id"]: edge for edge in payload["graph"]["edges"]}

        for update in expansion.get("node_updates", []):
            if not isinstance(update, dict):
                continue
            node = node_lookup.get(str(update.get("id") or "").strip())
            if not node:
                continue
            if update.get("summary"):
                node["summary"] = str(update["summary"]).strip()
                node["description"] = node["summary"]
            if update.get("source_url"):
                node["source_url"] = str(update["source_url"]).strip()

        for update in expansion.get("edge_updates", []):
            if not isinstance(update, dict):
                continue
            edge = edge_lookup.get(str(update.get("id") or "").strip())
            if not edge:
                continue
            if update.get("explanation"):
                edge["explanation"] = str(update["explanation"]).strip()

        existing_ids = set(node_lookup)
        for addition in expansion.get("node_additions", [])[:4]:
            normalized = self._normalize_added_node(addition, snapshot)
            if not normalized or normalized["id"] in existing_ids:
                continue
            payload["graph"]["nodes"].append(normalized)
            node_lookup[normalized["id"]] = normalized
            existing_ids.add(normalized["id"])

        existing_edge_ids = set(edge_lookup)
        valid_node_ids = {node["id"] for node in payload["graph"]["nodes"]}
        for addition in expansion.get("edge_additions", [])[:6]:
            normalized = self._normalize_added_edge(addition)
            if not normalized or normalized["id"] in existing_edge_ids:
                continue
            if normalized["source"] not in valid_node_ids or normalized["target"] not in valid_node_ids:
                continue
            payload["graph"]["edges"].append(normalized)
            existing_edge_ids.add(normalized["id"])

    def _normalize_added_node(
        self,
        addition: Any,
        snapshot: PolymarketEventSnapshot,
    ) -> dict[str, Any] | None:
        if not isinstance(addition, dict):
            return None

        node_id = str(addition.get("id") or "").strip()
        label = str(addition.get("label") or "").strip()
        node_type = str(addition.get("type") or "").strip()
        if not node_id or not label or node_type not in ALLOWED_NODE_TYPES - {"Event", "Rule", "RelatedMarket"}:
            return None

        return {
            "id": node_id,
            "label": label,
            "type": node_type,
            "confidence": self._parse_confidence(addition.get("confidence"), default=0.66),
            "summary": str(addition.get("summary") or "").strip() or label,
            "metadata": self._clean_metadata(addition.get("metadata")),
            "evidence_snippets": self._clean_snippets(addition.get("evidence_snippets")),
            "source_url": str(addition.get("source_url") or "").strip()
            or ("" if node_type in ("Entity", "Evidence", "Hypothesis") else snapshot.canonical_url),
        }

    def _normalize_added_edge(self, addition: Any) -> dict[str, Any] | None:
        if not isinstance(addition, dict):
            return None

        edge_id = str(addition.get("id") or "").strip()
        source = str(addition.get("source") or "").strip()
        target = str(addition.get("target") or "").strip()
        edge_type = str(addition.get("type") or "").strip()
        if not edge_id or not source or not target or edge_type not in ALLOWED_EDGE_TYPES:
            return None

        return {
            "id": edge_id,
            "source": source,
            "target": target,
            "type": edge_type,
            "confidence": self._parse_confidence(addition.get("confidence"), default=0.66),
            "explanation": str(addition.get("explanation") or "").strip() or "Agent-added causal link.",
        }

    def _validate_payload(self, payload: dict[str, Any]) -> None:
        if not payload.get("event") or not payload["event"].get("source_url"):
            raise ValueError("Graph payload must include an event source URL.")

        assets = payload.get("assets", {})
        nodes = payload.get("graph", {}).get("nodes", [])
        edges = payload.get("graph", {}).get("edges", [])
        node_ids = {node["id"] for node in nodes}
        edge_ids = {edge["id"] for edge in edges}

        if len(node_ids) != len(nodes):
            raise ValueError("Graph payload contains duplicate node IDs.")
        if len(edge_ids) != len(edges):
            raise ValueError("Graph payload contains duplicate edge IDs.")

        conceptual_types = {"Entity", "Evidence", "Rule", "Hypothesis"}
        for node in nodes:
            if node.get("type") not in ALLOWED_NODE_TYPES:
                raise ValueError(f"Node {node['id']} uses an invalid type.")
            if not node.get("icon_key") or node["icon_key"] not in assets:
                raise ValueError(f"Node {node['id']} is missing a valid icon asset.")
            if not node.get("source_url") and node.get("type") not in conceptual_types:
                raise ValueError(f"Node {node['id']} is missing a source URL.")
            if not node.get("summary"):
                raise ValueError(f"Node {node['id']} is missing a summary.")
            if not node.get("source_description"):
                raise ValueError(f"Node {node['id']} is missing a source description.")

        for edge in edges:
            if edge.get("type") not in ALLOWED_EDGE_TYPES:
                raise ValueError(f"Edge {edge['id']} uses an invalid type.")
            if edge["source"] not in node_ids or edge["target"] not in node_ids:
                raise ValueError(f"Edge {edge['id']} references a missing node.")

    def _deterministic_review(self, payload: dict[str, Any]) -> dict[str, Any]:
        nodes = payload.get("graph", {}).get("nodes", [])
        edges = payload.get("graph", {}).get("edges", [])
        issues = []
        if len(nodes) < 6:
            issues.append("Graph is too small to feel like a real butterfly analysis run.")
        if not any(node["type"] == "RelatedMarket" for node in nodes):
            issues.append("No related-market nodes were discovered.")
        if not any(edge["type"] == "affects_indirectly" for edge in edges):
            issues.append("No indirect-impact edges are present.")
        return {
            "approved": not issues,
            "issues": issues,
            "follow_up_actions": [
                "Enable Anthropic and rerun the graph for deeper expansion."
            ]
            if issues
            else ["No deterministic structural issues were found."],
            "quality_score": 0.88 if not issues else 0.63,
        }

    def _graph_stats(self, payload: dict[str, Any]) -> dict[str, int]:
        nodes = payload.get("graph", {}).get("nodes", [])
        edges = payload.get("graph", {}).get("edges", [])
        return {
            "nodes": len(nodes),
            "edges": len(edges),
            "related_markets": sum(1 for node in nodes if node["type"] == "RelatedMarket"),
            "evidence_nodes": sum(1 for node in nodes if node["type"] == "Evidence"),
        }

    def _get_source_context(
        self,
        source_url: str,
        snapshot: PolymarketEventSnapshot,
        source_cache: dict[str, Any],
    ) -> dict[str, str]:
        source_url = source_url or snapshot.canonical_url
        if source_url in source_cache:
            cached = source_cache[source_url]
            return self._context_from_snapshot(cached)

        if not self._is_polymarket_url(source_url):
            context = {
                "title": urlparse(source_url).netloc.replace("www.", "") or "External source",
                "description": "External source linked into the ChaosWing graph.",
                "image_url": "",
            }
            source_cache[source_url] = context
            return context

        try:
            resolved = self.metadata_service.hydrate(source_url)
        except Exception:
            resolved = snapshot
        source_cache[source_url] = resolved
        return self._context_from_snapshot(resolved)

    def _context_from_snapshot(self, snapshot_or_context: Any) -> dict[str, str]:
        if isinstance(snapshot_or_context, PolymarketEventSnapshot):
            return {
                "title": snapshot_or_context.title,
                "description": snapshot_or_context.description,
                "image_url": snapshot_or_context.icon_url or snapshot_or_context.image_url,
            }
        return {
            "title": str(snapshot_or_context.get("title") or ""),
            "description": str(snapshot_or_context.get("description") or ""),
            "image_url": str(snapshot_or_context.get("image_url") or ""),
        }

    def _fetch_snapshot_icon(self, snapshot: PolymarketEventSnapshot) -> str:
        image_url = snapshot.icon_url or snapshot.image_url
        if not image_url or not settings.CHAOSWING_ENABLE_REMOTE_FETCH:
            return ""
        try:
            return fetch_remote_image_data_uri(image_url)
        except Exception:
            return ""

    def _asset_slug(self, source_url: str, fallback: str) -> str:
        parsed = urlparse(source_url or "")
        tail = (parsed.path.strip("/").split("/")[-1] if parsed.path else "") or parsed.netloc or fallback
        return "".join(ch if ch.isalnum() else "-" for ch in tail.lower()).strip("-")[:48] or fallback

    def _parse_confidence(self, value: Any, default: float) -> float:
        try:
            confidence = float(value)
        except (TypeError, ValueError):
            confidence = default
        return round(max(0.0, min(confidence, 1.0)), 2)

    def _clean_metadata(self, items: Any) -> list[dict[str, str]]:
        if not isinstance(items, list):
            return []
        cleaned = []
        for item in items:
            if not isinstance(item, dict):
                continue
            label = str(item.get("label") or "").strip()
            value = str(item.get("value") or "").strip()
            if label and value:
                cleaned.append({"label": label, "value": value})
        return cleaned

    def _clean_snippets(self, items: Any) -> list[str]:
        if not isinstance(items, list):
            return []
        return [str(item).strip() for item in items if str(item).strip()]

    def _is_polymarket_url(self, url: str) -> bool:
        parsed = urlparse(url)
        return bool(parsed.scheme in {"http", "https"} and "polymarket.com" in parsed.netloc)

    def _iso_now(self) -> str:
        return datetime.now(tz=UTC).isoformat().replace("+00:00", "Z")

    def _elapsed_ms(self, started_at: float) -> int:
        return max(int((perf_counter() - started_at) * 1000), 1)

    def _finalize_pipeline_trace(
        self,
        record: PipelineStageRecord,
        *,
        started_at: float,
        metadata: dict[str, Any] | None = None,
        citations: list[str] | None = None,
    ) -> PipelineStageRecord:
        record.latency_ms = max(record.latency_ms, self._elapsed_ms(started_at))
        if metadata:
            record.metadata = {**record.metadata, **metadata}
        if citations:
            merged = list(record.citations)
            for citation in citations:
                if citation and citation not in merged:
                    merged.append(citation)
            record.citations = merged
        return record

    def _trace_override(
        self,
        *,
        latency_ms: int,
        metadata: dict[str, Any] | None = None,
        citations: list[str] | None = None,
        token_input: int = 0,
        token_output: int = 0,
        cost_usd: float = 0.0,
    ) -> dict[str, Any]:
        return {
            "latency_ms": max(int(latency_ms or 0), 0),
            "token_input": max(int(token_input or 0), 0),
            "token_output": max(int(token_output or 0), 0),
            "cost_usd": max(float(cost_usd or 0.0), 0.0),
            "citations": list(citations or []),
            "metadata": metadata or {},
        }

    def _persist_run_artifacts(
        self,
        run: GraphRun,
        snapshot: PolymarketEventSnapshot,
        payload: dict[str, Any],
        graph_stats: dict[str, int],
        workflow_log: list[dict[str, str]],
        *,
        trace_overrides: dict[str, dict[str, Any]] | None = None,
        extra_traces: list[PipelineStageRecord] | None = None,
    ) -> None:
        snapshot_record = MarketSnapshot.objects.create(
            graph_run=run,
            source_url=snapshot.canonical_url,
            event_slug=snapshot.slug,
            event_title=snapshot.title,
            status=snapshot.status,
            category=snapshot.category,
            source_kind=snapshot.source_kind,
            tags=snapshot.tags,
            outcomes=snapshot.outcomes,
            implied_probability=self._event_implied_probability(payload, snapshot),
            volume=snapshot.volume,
            liquidity=snapshot.liquidity,
            open_interest=snapshot.open_interest,
            related_market_count=graph_stats.get("related_markets", 0),
            evidence_count=graph_stats.get("evidence_nodes", 0),
            snapshot_at=self._parse_snapshot_datetime(snapshot.updated_at),
            payload={
                "snapshot": snapshot.as_dict(),
                "graph_stats": graph_stats,
                "review": payload.get("run", {}).get("review", {}),
            },
        )
        self._maybe_label_resolution(snapshot_record, snapshot)
        traces = [
            self._build_agent_trace(
                run,
                stage=str(entry.get("step") or "workflow_step"),
                status=str(entry.get("status") or "completed"),
                detail=str(entry.get("detail") or "").strip(),
                metadata={
                    "source_kind": snapshot.source_kind,
                    "mode": payload.get("run", {}).get("mode", ""),
                    **((trace_overrides or {}).get(str(entry.get("step") or ""), {}).get("metadata") or {}),
                },
                latency_ms=int(
                    ((trace_overrides or {}).get(str(entry.get("step") or ""), {}).get("latency_ms") or 0)
                ),
                token_input=int(
                    ((trace_overrides or {}).get(str(entry.get("step") or ""), {}).get("token_input") or 0)
                ),
                token_output=int(
                    ((trace_overrides or {}).get(str(entry.get("step") or ""), {}).get("token_output") or 0)
                ),
                cost_usd=float(
                    ((trace_overrides or {}).get(str(entry.get("step") or ""), {}).get("cost_usd") or 0.0)
                ),
                citations=list(
                    ((trace_overrides or {}).get(str(entry.get("step") or ""), {}).get("citations") or [])
                ),
            )
            for entry in workflow_log
        ]
        for stage_record in extra_traces or []:
            traces.append(
                self._build_agent_trace(
                    run,
                    stage=stage_record.stage,
                    status=stage_record.status,
                    detail=stage_record.detail,
                    metadata=stage_record.metadata,
                    latency_ms=stage_record.latency_ms,
                    token_input=stage_record.token_input,
                    token_output=stage_record.token_output,
                    cost_usd=stage_record.cost_usd,
                    citations=stage_record.citations,
                )
            )
        if traces:
            AgentTrace.objects.bulk_create(traces)

    def _event_implied_probability(
        self,
        payload: dict[str, Any],
        snapshot: PolymarketEventSnapshot,
    ) -> float:
        event_node = next(
            (node for node in payload.get("graph", {}).get("nodes", []) if node.get("type") == "Event"),
            None,
        )
        if event_node is not None:
            return self._parse_confidence(event_node.get("probability"), default=0.0)
        for market in snapshot.markets:
            if market.outcome_prices:
                return self._parse_confidence(market.outcome_prices[0], default=0.0)
        return 0.0

    def _parse_snapshot_datetime(self, value: str) -> datetime:
        if value:
            clean = str(value).strip()
            if clean:
                try:
                    return datetime.fromisoformat(clean.replace("Z", "+00:00"))
                except ValueError:
                    pass
        return datetime.now(tz=UTC)

    def _maybe_label_resolution(
        self,
        snapshot_record: MarketSnapshot,
        snapshot: PolymarketEventSnapshot,
    ) -> None:
        self.resolution_labeling.label_from_terminal_snapshot(
            record=snapshot_record,
            terminal_snapshot=snapshot,
            source="outcome_prices",
            metadata={"status": snapshot.status, "collector": "graph_workflow"},
        )

    def _create_agent_trace(
        self,
        run: GraphRun,
        *,
        stage: str,
        status: str,
        detail: str,
        metadata: dict[str, Any] | None = None,
        latency_ms: int = 0,
        token_input: int = 0,
        token_output: int = 0,
        cost_usd: float = 0.0,
        citations: list[str] | None = None,
    ) -> None:
        AgentTrace.objects.create(
            **self._agent_trace_kwargs(
                run,
                stage=stage,
                status=status,
                detail=detail,
                metadata=metadata,
                latency_ms=latency_ms,
                token_input=token_input,
                token_output=token_output,
                cost_usd=cost_usd,
                citations=citations,
            )
        )

    def _build_agent_trace(
        self,
        run: GraphRun,
        *,
        stage: str,
        status: str,
        detail: str,
        metadata: dict[str, Any] | None = None,
        latency_ms: int = 0,
        token_input: int = 0,
        token_output: int = 0,
        cost_usd: float = 0.0,
        citations: list[str] | None = None,
    ) -> AgentTrace:
        return AgentTrace(
            **self._agent_trace_kwargs(
                run,
                stage=stage,
                status=status,
                detail=detail,
                metadata=metadata,
                latency_ms=latency_ms,
                token_input=token_input,
                token_output=token_output,
                cost_usd=cost_usd,
                citations=citations,
            )
        )

    def _agent_trace_kwargs(
        self,
        run: GraphRun,
        *,
        stage: str,
        status: str,
        detail: str,
        metadata: dict[str, Any] | None = None,
        latency_ms: int = 0,
        token_input: int = 0,
        token_output: int = 0,
        cost_usd: float = 0.0,
        citations: list[str] | None = None,
    ) -> dict[str, Any]:
        return {
            "graph_run": run,
            "stage": stage,
            "status": status,
            "detail": detail,
            "latency_ms": latency_ms,
            "token_input": token_input,
            "token_output": token_output,
            "cost_usd": cost_usd,
            "citations": [citation for citation in (citations or []) if str(citation or "").strip()],
            "metadata": metadata or {},
        }

    def _pop_llm_trace(self, payload: dict[str, Any] | None) -> dict[str, Any]:
        if not isinstance(payload, dict):
            return {}
        trace = payload.pop("_llm_trace", {})
        return trace if isinstance(trace, dict) else {}

    def _trace_override_from_llm(
        self,
        trace: dict[str, Any],
        *,
        citations: list[str] | None = None,
        metadata: dict[str, Any] | None = None,
    ) -> dict[str, Any]:
        return {
            "latency_ms": int(trace.get("latency_ms", 0) or 0),
            "token_input": int(trace.get("token_input", 0) or 0),
            "token_output": int(trace.get("token_output", 0) or 0),
            "cost_usd": float(trace.get("cost_usd", 0.0) or 0.0),
            "citations": citations or [],
            "metadata": {
                **self._trace_metadata_from_llm(trace),
                **(metadata or {}),
            },
        }

    def _trace_metadata_from_llm(self, trace: dict[str, Any]) -> dict[str, Any]:
        metadata = {
            "provider": str(trace.get("provider") or "").strip(),
            "model": str(trace.get("model") or "").strip(),
            "response_id": str(trace.get("response_id") or "").strip(),
            "stop_reason": str(trace.get("stop_reason") or "").strip(),
            "estimated_input_tokens": int(trace.get("estimated_input_tokens", 0) or 0),
        }
        return {key: value for key, value in metadata.items() if value not in {"", 0}}

    def _trace_citations_from_expansion(self, expansion: dict[str, Any]) -> list[str]:
        citations: list[str] = []
        for collection_key in ("node_additions", "node_updates"):
            for item in expansion.get(collection_key, []):
                if not isinstance(item, dict):
                    continue
                url = str(item.get("source_url") or "").strip()
                if url and url not in citations:
                    citations.append(url)
        return citations
