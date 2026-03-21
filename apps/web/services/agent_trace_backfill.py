from __future__ import annotations

from collections import defaultdict
from copy import deepcopy
from typing import Any

from django.db.models import Prefetch
from django.utils import timezone

from apps.web.models import AgentTrace, GraphRun

from .agent_pipeline import GraphAgentPipeline, PipelineStageRecord
from .anthropic_agent import AnthropicGraphAgent
from .contracts import PolymarketEventSnapshot, RelatedEventCandidate
from .market_intelligence import BenchmarkSummaryService

REQUIRED_AGENT_STAGES = ("planner", "retriever", "graph_editor", "critic", "verifier")


class AgentTraceBackfillService:
    def __init__(self, pipeline: GraphAgentPipeline | None = None):
        self.pipeline = pipeline or GraphAgentPipeline()
        self.pricing_agent = AnthropicGraphAgent(enabled=False)

    def run(
        self,
        *,
        limit: int | None = None,
        overwrite: bool = False,
        update_payload: bool = True,
    ) -> dict[str, Any]:
        queryset = GraphRun.objects.prefetch_related(
            Prefetch("agent_traces", queryset=AgentTrace.objects.order_by("created_at"))
        ).order_by("-created_at")
        if limit:
            queryset = queryset[:limit]

        summary = {
            "run_count": 0,
            "runs_with_new_stage_traces": 0,
            "runs_with_payload_updates": 0,
            "runs_with_trace_repairs": 0,
            "stage_traces_created": 0,
            "trace_repairs": 0,
            "stages_created": defaultdict(int),
            "runs_skipped": 0,
        }
        for run in queryset:
            summary["run_count"] += 1
            result = self.backfill_run(run, overwrite=overwrite, update_payload=update_payload)
            summary["stage_traces_created"] += result["created_trace_count"]
            summary["trace_repairs"] += result["repaired_trace_count"]
            if result["created_trace_count"]:
                summary["runs_with_new_stage_traces"] += 1
            if result["repaired_trace_count"]:
                summary["runs_with_trace_repairs"] += 1
            if result["payload_updated"]:
                summary["runs_with_payload_updates"] += 1
            if (
                not result["created_trace_count"]
                and not result["payload_updated"]
                and not result["repaired_trace_count"]
            ):
                summary["runs_skipped"] += 1
            for stage, count in result["created_stages"].items():
                summary["stages_created"][stage] += count

        summary["stages_created"] = dict(sorted(summary["stages_created"].items()))
        return summary

    def backfill_run(
        self,
        run: GraphRun,
        *,
        overwrite: bool = False,
        update_payload: bool = True,
    ) -> dict[str, Any]:
        payload = deepcopy(run.payload or {})
        pipeline_records = self._reconstruct_pipeline_records(run, payload)
        if not pipeline_records:
            return {
                "payload_updated": False,
                "created_trace_count": 0,
                "created_stages": {},
            }

        existing_pipeline = self._payload_stage_records(payload)
        payload_updated = False
        reconstructed_payload = [
            pipeline_records[stage].as_dict() for stage in REQUIRED_AGENT_STAGES if stage in pipeline_records
        ]
        existing_payload = [
            existing_pipeline[stage].as_dict() for stage in REQUIRED_AGENT_STAGES if stage in existing_pipeline
        ]
        if update_payload and (overwrite or existing_payload != reconstructed_payload):
            payload.setdefault("run", {})["agent_pipeline"] = [
                pipeline_records[stage].as_dict() for stage in REQUIRED_AGENT_STAGES if stage in pipeline_records
            ]
            run.payload = payload
            run.updated_at = timezone.now()
            run.save(update_fields=["payload", "updated_at"])
            payload_updated = True

        existing_by_stage = defaultdict(list)
        for trace in run.agent_traces.all():
            if trace.stage in REQUIRED_AGENT_STAGES:
                existing_by_stage[trace.stage].append(trace)
        if overwrite:
            run.agent_traces.filter(stage__in=REQUIRED_AGENT_STAGES).delete()
            existing_by_stage.clear()

        created_stages: dict[str, int] = {}
        for stage in REQUIRED_AGENT_STAGES:
            record = pipeline_records.get(stage)
            if not record:
                continue
            existing_traces = existing_by_stage.get(stage, [])
            if len(existing_traces) > 1:
                keep = existing_traces[-1]
                run.agent_traces.filter(stage=stage).exclude(id=keep.id).delete()
                existing_traces = [keep]
                existing_by_stage[stage] = existing_traces
            if existing_traces and not overwrite:
                continue
            defaults = {
                "status": record.status,
                "detail": record.detail,
                "latency_ms": record.latency_ms,
                "token_input": record.token_input,
                "token_output": record.token_output,
                "cost_usd": record.cost_usd,
                "citations": record.citations,
                "metadata": record.metadata,
            }
            if existing_traces:
                trace = existing_traces[0]
                for key, value in defaults.items():
                    setattr(trace, key, value)
                trace.save(
                    update_fields=[
                        "status",
                        "detail",
                        "latency_ms",
                        "token_input",
                        "token_output",
                        "cost_usd",
                        "citations",
                        "metadata",
                    ]
                )
                created = False
            else:
                trace = AgentTrace.objects.create(
                    graph_run=run,
                    stage=record.stage,
                    **defaults,
                )
                created = True
            existing_by_stage[stage] = [trace]
            if created:
                created_stages[stage] = created_stages.get(stage, 0) + 1

        created_trace_count = sum(created_stages.values())
        repaired_trace_count = self._repair_existing_trace_telemetry(run)
        if created_trace_count or repaired_trace_count:
            BenchmarkSummaryService.invalidate_cached_summary()

        return {
            "payload_updated": payload_updated,
            "created_trace_count": created_trace_count,
            "created_stages": created_stages,
            "repaired_trace_count": repaired_trace_count,
        }

    def _reconstruct_pipeline_records(
        self,
        run: GraphRun,
        payload: dict[str, Any],
    ) -> dict[str, PipelineStageRecord]:
        records = self._payload_stage_records(payload)
        snapshot = self._snapshot_from_run(run, payload)
        related_candidates = self._related_candidates_from_run(run, payload)
        legacy_traces = self._legacy_trace_map(run)
        workflow_steps = self._workflow_map(run, payload)

        if "planner" not in records:
            records["planner"] = self.pipeline.plan(snapshot, payload, related_candidates)
        if "retriever" not in records:
            records["retriever"] = self.pipeline.retrieve(snapshot, related_candidates)
        if "graph_editor" not in records:
            records["graph_editor"] = self._graph_editor_record(
                run,
                payload,
                workflow_steps=workflow_steps,
                legacy_traces=legacy_traces,
            )
        if "critic" not in records:
            records["critic"] = self._critic_record(payload, workflow_steps=workflow_steps, legacy_traces=legacy_traces)
        if "verifier" not in records:
            records["verifier"] = self.pipeline.verify(payload)
        return records

    def _payload_stage_records(self, payload: dict[str, Any]) -> dict[str, PipelineStageRecord]:
        records: dict[str, PipelineStageRecord] = {}
        for item in (payload.get("run", {}) or {}).get("agent_pipeline") or []:
            if not isinstance(item, dict):
                continue
            stage = str(item.get("stage") or "").strip()
            if not stage:
                continue
            records[stage] = PipelineStageRecord(
                stage=stage,
                status=str(item.get("status") or "completed"),
                detail=str(item.get("detail") or "").strip(),
                metadata=item.get("metadata") if isinstance(item.get("metadata"), dict) else {},
                latency_ms=int(item.get("latency_ms") or 0),
                token_input=int(item.get("token_input") or 0),
                token_output=int(item.get("token_output") or 0),
                cost_usd=float(item.get("cost_usd") or 0.0),
                citations=[str(url) for url in item.get("citations") or [] if str(url).strip()],
            )
        return records

    def _snapshot_from_run(self, run: GraphRun, payload: dict[str, Any]) -> PolymarketEventSnapshot:
        snapshot_payload = (
            ((payload.get("context") or {}).get("source_snapshot") or {})
            if isinstance(payload.get("context"), dict)
            else {}
        )
        if not snapshot_payload:
            snapshot_payload = deepcopy(run.source_snapshot or {})
        if not snapshot_payload:
            event = payload.get("event") or {}
            snapshot_payload = {
                "source_url": run.source_url,
                "canonical_url": run.source_url,
                "slug": run.event_slug,
                "title": run.event_title or event.get("title") or run.source_url,
                "description": event.get("description") or "",
                "status": event.get("status") or "open",
                "category": event.get("category") or "",
                "tags": event.get("tags") or [],
                "outcomes": event.get("outcomes") or [],
                "source_kind": "persisted-run",
            }
        snapshot_payload.setdefault("source_url", run.source_url)
        snapshot_payload.setdefault("canonical_url", run.source_url)
        snapshot_payload.setdefault("slug", run.event_slug)
        snapshot_payload.setdefault("title", run.event_title or run.source_url)
        snapshot_payload.setdefault("status", "open")
        snapshot_payload.setdefault("category", "")
        snapshot_payload.setdefault("source_kind", "persisted-run")
        return PolymarketEventSnapshot.from_dict(snapshot_payload)

    def _related_candidates_from_run(
        self,
        run: GraphRun,
        payload: dict[str, Any],
    ) -> list[RelatedEventCandidate]:
        context = payload.get("context") or {}
        if isinstance(context, dict):
            serialized = context.get("related_candidates") or []
            candidates = [
                RelatedEventCandidate.from_dict(item)
                for item in serialized
                if isinstance(item, dict)
            ]
            if candidates:
                return candidates

        event = payload.get("event") or {}
        source_url = str(event.get("source_url") or run.source_url or "")
        candidates = []
        seen = set()
        for node in (payload.get("graph") or {}).get("nodes") or []:
            if node.get("type") != "RelatedMarket":
                continue
            candidate_url = str(node.get("source_url") or "").strip()
            title = str(node.get("label") or "").strip()
            key = candidate_url or title
            if not key or key in seen:
                continue
            seen.add(key)
            if candidate_url == source_url:
                continue
            candidate_snapshot = PolymarketEventSnapshot.from_dict(
                {
                    "source_url": candidate_url,
                    "canonical_url": candidate_url,
                    "slug": str(node.get("id") or ""),
                    "title": title,
                    "description": str(node.get("summary") or node.get("description") or ""),
                    "status": "open",
                    "category": "",
                    "tags": [],
                    "outcomes": [],
                    "source_kind": "persisted-related-node",
                }
            )
            candidates.append(
                RelatedEventCandidate(
                    snapshot=candidate_snapshot,
                    confidence=float(node.get("confidence") or 0.0),
                    rationale="Recovered from persisted related-market node.",
                )
            )
        return candidates

    def _legacy_trace_map(self, run: GraphRun) -> dict[str, AgentTrace]:
        traces: dict[str, AgentTrace] = {}
        for trace in run.agent_traces.all():
            traces[trace.stage] = trace
        return traces

    def _workflow_map(self, run: GraphRun, payload: dict[str, Any]) -> dict[str, dict[str, Any]]:
        workflow = (payload.get("run") or {}).get("workflow") or run.workflow_log or []
        steps: dict[str, dict[str, Any]] = {}
        for item in workflow:
            if not isinstance(item, dict):
                continue
            step = str(item.get("step") or "").strip()
            if step:
                steps[step] = item
        return steps

    def _graph_editor_record(
        self,
        run: GraphRun,
        payload: dict[str, Any],
        *,
        workflow_steps: dict[str, dict[str, Any]],
        legacy_traces: dict[str, AgentTrace],
    ) -> PipelineStageRecord:
        step = workflow_steps.get("llm_expansion", {})
        legacy_trace = legacy_traces.get("llm_expansion")
        status = str(step.get("status") or "").strip() or (
            legacy_trace.status if legacy_trace else ("completed" if run.mode == "agent-enriched" else "skipped")
        )
        if status == "completed":
            detail = str(step.get("detail") or "").strip() or "Recovered graph-editor stage from saved workflow output."
        elif status == "failed":
            detail = str(step.get("detail") or "").strip() or "Recovered failed graph-editor stage from saved workflow output."
        else:
            detail = str(step.get("detail") or "").strip() or "No model-authored graph edits were applied for this run."

        citations = []
        if legacy_trace and legacy_trace.citations:
            citations = [str(url) for url in legacy_trace.citations if str(url).strip()]
        if not citations:
            citations = self._graph_citations(payload)

        metadata = {
            "recovered": True,
            "mode": run.mode,
            "model_name": run.model_name,
        }
        if legacy_trace:
            metadata.update(legacy_trace.metadata or {})
        return PipelineStageRecord(
            stage="graph_editor",
            status=status,
            detail=detail,
            metadata={key: value for key, value in metadata.items() if value not in {"", None}},
            latency_ms=legacy_trace.latency_ms if legacy_trace else 0,
            token_input=legacy_trace.token_input if legacy_trace else 0,
            token_output=legacy_trace.token_output if legacy_trace else 0,
            cost_usd=legacy_trace.cost_usd if legacy_trace else 0.0,
            citations=citations[:8],
        )

    def _critic_record(
        self,
        payload: dict[str, Any],
        *,
        workflow_steps: dict[str, dict[str, Any]],
        legacy_traces: dict[str, AgentTrace],
    ) -> PipelineStageRecord:
        review = ((payload.get("run") or {}).get("review") or {}) if isinstance(payload.get("run"), dict) else {}
        if not isinstance(review, dict) or not review:
            return PipelineStageRecord(
                stage="critic",
                status="skipped",
                detail="No saved review payload was available to reconstruct the critic stage.",
            )
        llm_trace = self._legacy_llm_trace(legacy_traces.get("llm_review"))
        record = self.pipeline.critic(review, llm_trace=llm_trace)
        step = workflow_steps.get("llm_review", {})
        if step:
            record.status = str(step.get("status") or record.status)
            if str(step.get("detail") or "").strip():
                record.detail = str(step.get("detail")).strip()
        metadata = {"recovered": True, **record.metadata}
        record.metadata = metadata
        return record

    def _legacy_llm_trace(self, trace: AgentTrace | None) -> dict[str, Any] | None:
        if not trace:
            return None
        metadata = trace.metadata or {}
        return {
            "provider": metadata.get("provider"),
            "model": metadata.get("model"),
            "response_id": metadata.get("response_id"),
            "stop_reason": metadata.get("stop_reason"),
            "latency_ms": trace.latency_ms,
            "token_input": trace.token_input,
            "token_output": trace.token_output,
            "cost_usd": trace.cost_usd,
        }

    def _repair_existing_trace_telemetry(self, run: GraphRun) -> int:
        repaired = 0
        for trace in run.agent_traces.all():
            update_fields: list[str] = []
            metadata = dict(trace.metadata or {})
            tokens = int(trace.token_input or 0) + int(trace.token_output or 0)
            execution_mode = str(metadata.get("execution_mode") or "").strip().lower()
            model_name = str(metadata.get("model") or run.model_name or "").strip()
            provider = str(metadata.get("provider") or "").strip().lower()

            if tokens > 0 and not execution_mode:
                metadata["execution_mode"] = "anthropic"
                execution_mode = "anthropic"
            if tokens > 0 and model_name and not metadata.get("model"):
                metadata["model"] = model_name
            if tokens > 0 and not provider and (
                execution_mode == "anthropic" or model_name.startswith("claude")
            ):
                metadata["provider"] = "anthropic"
                provider = "anthropic"
            if metadata != (trace.metadata or {}):
                trace.metadata = metadata
                update_fields.append("metadata")

            if (
                trace.cost_usd <= 0
                and tokens > 0
                and (provider == "anthropic" or str(metadata.get("model") or "").startswith("claude"))
            ):
                self.pricing_agent.model = str(metadata.get("model") or run.model_name or self.pricing_agent.model)
                estimated_cost = self.pricing_agent._estimate_cost_usd(
                    input_tokens=int(trace.token_input or 0),
                    output_tokens=int(trace.token_output or 0),
                )
                if estimated_cost > 0:
                    trace.cost_usd = estimated_cost
                    update_fields.append("cost_usd")

            if update_fields:
                trace.save(update_fields=sorted(set(update_fields)))
                repaired += 1
        return repaired

    def _graph_citations(self, payload: dict[str, Any]) -> list[str]:
        citations: list[str] = []
        for node in (payload.get("graph") or {}).get("nodes") or []:
            url = str(node.get("source_url") or "").strip()
            if url and url not in citations:
                citations.append(url)
        return citations
