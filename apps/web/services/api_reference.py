from __future__ import annotations

import json
from dataclasses import dataclass, field
from typing import Any
from urllib.parse import urlencode

from django.conf import settings
from django.urls import reverse
from django.utils.text import slugify

from chaoswing.middleware import RateLimitMiddleware


API_REFERENCE_TITLE = "ChaosWing API"
API_REFERENCE_VERSION = "v1"
OPENAPI_VERSION = "3.1.0"
SAMPLE_RUN_ID = "0123ec8b-189c-4fb6-a0f2-3bcc0a64dbbd"
SAMPLE_PAIR_ID = 7


@dataclass(frozen=True)
class ApiParameterDoc:
    name: str
    location: str
    required: bool
    schema_type: str
    description: str
    example: Any | None = None
    default: Any | None = None
    enum: tuple[Any, ...] = ()

    def schema(self) -> dict[str, Any]:
        schema: dict[str, Any] = {"type": self.schema_type}
        if self.default is not None:
            schema["default"] = self.default
        if self.example is not None:
            schema["example"] = self.example
        if self.enum:
            schema["enum"] = list(self.enum)
        return schema

    def to_openapi(self) -> dict[str, Any]:
        return {
            "name": self.name,
            "in": self.location,
            "required": self.required,
            "description": self.description,
            "schema": self.schema(),
        }


@dataclass(frozen=True)
class ApiFieldDoc:
    name: str
    field_type: str
    description: str
    required: bool = False
    example: Any | None = None

    def schema(self) -> dict[str, Any]:
        schema: dict[str, Any] = {"type": self.field_type}
        if self.example is not None:
            schema["example"] = self.example
        return schema


@dataclass(frozen=True)
class ApiRequestBodyDoc:
    description: str
    fields: tuple[ApiFieldDoc, ...]
    required: bool = True
    example: Any | None = None

    def schema(self) -> dict[str, Any]:
        properties = {field.name: field.schema() for field in self.fields}
        required_fields = [field.name for field in self.fields if field.required]
        schema: dict[str, Any] = {
            "type": "object",
            "properties": properties,
            "additionalProperties": False,
        }
        if required_fields:
            schema["required"] = required_fields
        return schema

    def to_openapi(self) -> dict[str, Any]:
        content = {
            "application/json": {
                "schema": self.schema(),
            }
        }
        if self.example is not None:
            content["application/json"]["example"] = self.example
        return {
            "required": self.required,
            "description": self.description,
            "content": content,
        }


@dataclass(frozen=True)
class ApiResponseDoc:
    status_code: int
    description: str
    schema: dict[str, Any]
    example: Any | None = None

    def to_openapi(self) -> dict[str, Any]:
        payload = {
            "description": self.description,
            "content": {
                "application/json": {
                    "schema": self.schema,
                }
            },
        }
        if self.example is not None:
            payload["content"]["application/json"]["example"] = self.example
        return payload


@dataclass(frozen=True)
class ApiEndpointDoc:
    name: str
    tag: str
    method: str
    path: str
    route_name: str
    summary: str
    description: str
    rate_limit_tier: str
    parameters: tuple[ApiParameterDoc, ...] = ()
    request_body: ApiRequestBodyDoc | None = None
    responses: tuple[ApiResponseDoc, ...] = ()
    notes: tuple[str, ...] = ()
    sample_path_values: dict[str, Any] = field(default_factory=dict)

    @property
    def operation_id(self) -> str:
        return self.route_name.replace(":", "_").replace("-", "_")

    @property
    def anchor(self) -> str:
        return slugify(f"{self.method}-{self.path}".replace("{", "").replace("}", ""))

    def example_path(self) -> str:
        return self.path.format(**self.sample_path_values)

    def example_query_params(self) -> dict[str, Any]:
        return {
            parameter.name: parameter.example
            for parameter in self.parameters
            if parameter.location == "query" and parameter.example is not None
        }

    def example_url(self, base_url: str) -> str:
        query_params = self.example_query_params()
        query_string = f"?{urlencode(query_params)}" if query_params else ""
        return f"{base_url}{self.example_path()}{query_string}"

    def curl_sample(self, base_url: str) -> str:
        lines = [
            f"curl -X {self.method} \\",
            f"  \"{self.example_url(base_url)}\"",
        ]
        if self.request_body:
            lines.insert(1, "  -H \"Content-Type: application/json\" \\")
            lines.append("  -d '" + json.dumps(self.request_body.example, indent=2) + "'")
        return "\n".join(lines)

    def fetch_sample(self, base_url: str) -> str:
        lines = [f"const response = await fetch(\"{self.example_url(base_url)}\", {{"]
        if self.method != "GET" or self.request_body:
            lines.append(f"  method: \"{self.method}\",")
        if self.request_body:
            lines.extend(
                [
                    "  headers: {",
                    "    \"Content-Type\": \"application/json\",",
                    "  },",
                    "  body: JSON.stringify("
                    + json.dumps(self.request_body.example, indent=2).replace("\n", "\n    ")
                    + "),",
                ]
            )
        lines.append("});")
        lines.append("const data = await response.json();")
        return "\n".join(lines)

    def to_openapi(self, base_url: str) -> dict[str, Any]:
        operation: dict[str, Any] = {
            "operationId": self.operation_id,
            "tags": [self.tag],
            "summary": self.summary,
            "description": self.description,
            "security": [],
            "responses": {str(response.status_code): response.to_openapi() for response in self.responses},
            "parameters": [parameter.to_openapi() for parameter in self.parameters],
            "x-codeSamples": [
                {"lang": "curl", "label": "cURL", "source": self.curl_sample(base_url)},
                {"lang": "JavaScript", "label": "fetch", "source": self.fetch_sample(base_url)},
            ],
            "x-chaoswing-rate-limit-tier": self.rate_limit_tier,
        }
        if self.request_body:
            operation["requestBody"] = self.request_body.to_openapi()
        if self.notes:
            operation["x-chaoswing-notes"] = list(self.notes)
        return operation


_ERROR_SCHEMA = {
    "type": "object",
    "properties": {
        "error": {"type": "string"},
    },
    "required": ["error"],
    "additionalProperties": False,
}

_RUN_SUMMARY_SCHEMA = {
    "type": "object",
    "properties": {
        "id": {"type": "string", "format": "uuid"},
        "event_title": {"type": "string"},
        "event_slug": {"type": "string"},
        "source_url": {"type": "string", "format": "uri"},
        "mode": {"type": "string"},
        "status": {"type": "string"},
        "graph_stats": {"type": "object"},
        "created_at": {"type": "string", "format": "date-time"},
        "updated_at": {"type": "string", "format": "date-time"},
        "detail_url": {"type": "string"},
        "brief_url": {"type": "string"},
        "related_markets_url": {"type": "string"},
        "changes_url": {"type": "string"},
    },
    "required": ["id", "event_title", "source_url", "mode", "status"],
}

_TAGS: tuple[dict[str, str], ...] = (
    {
        "name": "Discovery",
        "description": "Entry points for discovering the API surface and downloading the machine-readable schema.",
    },
    {
        "name": "Graph Runs",
        "description": "Generate a butterfly graph, load saved runs, inspect structured briefs, and trigger review passes.",
    },
    {
        "name": "Benchmarks",
        "description": "Read live benchmark summaries and manage the human review queue for ranking usefulness.",
    },
    {
        "name": "Lead-Lag",
        "description": "Inspect the cross-venue lead-lag research monitor, candidate signals, and pair diagnostics.",
    },
    {
        "name": "Markets",
        "description": "Access supporting market metadata such as trending contracts and link verification.",
    },
    {
        "name": "Watchlists",
        "description": "Load reusable trader watchlists built around macro, commodities, and politics workflows.",
    },
)


def _rate_limit_profile() -> list[dict[str, str]]:
    read_tier = RateLimitMiddleware.TIERS["api_read"]
    write_tier = RateLimitMiddleware.TIERS["api_write"]
    return [
        {
            "label": "API reads",
            "value": f"{read_tier['max_hits']} requests / {read_tier['window']}s",
            "copy": "Applies to GET requests under /api/.",
        },
        {
            "label": "API writes",
            "value": f"{write_tier['max_hits']} requests / {write_tier['window']}s",
            "copy": "Applies to POST requests under /api/.",
        },
        {
            "label": "Burst protection",
            "value": f"{RateLimitMiddleware.BURST_MAX} requests / {RateLimitMiddleware.BURST_WINDOW}s",
            "copy": "Exceeding the burst window triggers a temporary IP block in production.",
        },
    ]


def _format_bytes(num_bytes: int) -> str:
    if num_bytes >= 1_048_576:
        return f"{num_bytes / 1_048_576:.0f} MB"
    if num_bytes >= 1024:
        return f"{num_bytes / 1024:.0f} KB"
    return f"{num_bytes} bytes"


def _schema_field_rows(schema: dict[str, Any]) -> list[dict[str, Any]]:
    properties = schema.get("properties", {})
    required = set(schema.get("required", []))
    rows: list[dict[str, Any]] = []
    for name, field_schema in properties.items():
        rows.append(
            {
                "name": name,
                "type": field_schema.get("type", "object"),
                "required": name in required,
            }
        )
    return rows


def _endpoint_docs() -> tuple[ApiEndpointDoc, ...]:
    return (
        ApiEndpointDoc(
            name="API root",
            tag="Discovery",
            method="GET",
            path="/api/",
            route_name="web:api_root",
            summary="Discover the public API surface.",
            description=(
                "Returns the current public API version, docs links, rate-limit summary, "
                "and a machine-readable list of exposed resources."
            ),
            rate_limit_tier="api_read",
            responses=(
                ApiResponseDoc(
                    status_code=200,
                    description="Discovery document for the current public API.",
                    schema={
                        "type": "object",
                        "properties": {
                            "name": {"type": "string"},
                            "version": {"type": "string"},
                            "docs_url": {"type": "string"},
                            "openapi_url": {"type": "string"},
                            "resources": {"type": "array", "items": {"type": "object"}},
                        },
                        "required": ["name", "version", "docs_url", "openapi_url", "resources"],
                    },
                    example={
                        "name": API_REFERENCE_TITLE,
                        "version": API_REFERENCE_VERSION,
                        "docs_url": "/developers/api/",
                        "openapi_url": "/api/openapi.json",
                        "resources": [
                            {
                                "tag": "Graph Runs",
                                "method": "POST",
                                "path": "/api/v1/graph/from-url/",
                                "summary": "Generate and persist a graph run from one Polymarket URL.",
                            }
                        ],
                    },
                ),
            ),
            notes=(
                "Responses include Link headers pointing to the human-readable docs page and the OpenAPI description.",
            ),
        ),
        ApiEndpointDoc(
            name="OpenAPI schema",
            tag="Discovery",
            method="GET",
            path="/api/openapi.json",
            route_name="web:openapi_spec",
            summary="Download the OpenAPI 3.1 description.",
            description=(
                "Returns the machine-readable contract used to render the ChaosWing API reference."
            ),
            rate_limit_tier="api_read",
            responses=(
                ApiResponseDoc(
                    status_code=200,
                    description="OpenAPI schema for the public API.",
                    schema={
                        "type": "object",
                        "properties": {
                            "openapi": {"type": "string"},
                            "info": {"type": "object"},
                            "paths": {"type": "object"},
                            "tags": {"type": "array", "items": {"type": "object"}},
                        },
                        "required": ["openapi", "info", "paths"],
                    },
                    example={
                        "openapi": OPENAPI_VERSION,
                        "info": {"title": API_REFERENCE_TITLE, "version": API_REFERENCE_VERSION},
                        "paths": {"/api/v1/graph/from-url/": {"post": {"summary": "Generate a graph run"}}},
                    },
                ),
            ),
        ),
        ApiEndpointDoc(
            name="Generate graph run",
            tag="Graph Runs",
            method="POST",
            path="/api/v1/graph/from-url/",
            route_name="web:graph_from_url",
            summary="Generate and persist a graph run from one Polymarket URL.",
            description=(
                "Resolves the source event, builds the graph payload, stores the run, and returns "
                "the shareable run identifiers plus the graph content needed by the workspace."
            ),
            rate_limit_tier="api_write",
            request_body=ApiRequestBodyDoc(
                description="Polymarket event URL to resolve.",
                fields=(
                    ApiFieldDoc(
                        name="url",
                        field_type="string",
                        description="Full Polymarket event URL.",
                        required=True,
                        example="https://polymarket.com/event/fed-decision-in-march-885",
                    ),
                ),
                example={"url": "https://polymarket.com/event/fed-decision-in-march-885"},
            ),
            responses=(
                ApiResponseDoc(
                    status_code=200,
                    description="Graph run generated successfully.",
                    schema={
                        "type": "object",
                        "properties": {
                            "event": {"type": "object"},
                            "run": {"type": "object"},
                            "context": {"type": "object"},
                            "assets": {"type": "object"},
                            "graph": {"type": "object"},
                        },
                        "required": ["event", "run", "graph"],
                    },
                    example={
                        "event": {
                            "title": "Fed decision in March",
                            "status": "open",
                            "source_url": "https://polymarket.com/event/fed-decision-in-march-885",
                            "tags": ["Fed", "Rates", "Macro"],
                        },
                        "run": {
                            "id": SAMPLE_RUN_ID,
                            "mode": "resolved-backend",
                            "graph_stats": {"nodes": 14, "edges": 19, "related_markets": 5},
                            "detail_url": f"/api/v1/runs/{SAMPLE_RUN_ID}/",
                            "brief_url": f"/briefs/{SAMPLE_RUN_ID}/",
                        },
                        "graph": {
                            "nodes": [{"id": "evt_001", "label": "Fed decision in March", "type": "Event"}],
                            "edges": [{"id": "edge_1", "source": "evt_001", "target": "mkt_2", "type": "related_to"}],
                        },
                    },
                ),
                ApiResponseDoc(
                    status_code=400,
                    description="Request validation failed.",
                    schema=_ERROR_SCHEMA,
                    example={"error": "Use a full Polymarket event URL."},
                ),
                ApiResponseDoc(
                    status_code=500,
                    description="Graph generation failed.",
                    schema=_ERROR_SCHEMA,
                    example={"error": "ChaosWing could not generate a graph run."},
                ),
            ),
            notes=(
                "ChaosWing accepts JSON requests for public integrations; POST endpoints are CSRF-exempt so server-to-server clients can call them directly.",
            ),
        ),
        ApiEndpointDoc(
            name="List runs",
            tag="Graph Runs",
            method="GET",
            path="/api/v1/runs/",
            route_name="web:list_graph_runs",
            summary="List recent saved graph runs.",
            description="Returns the most recent persisted runs in reverse chronological order.",
            rate_limit_tier="api_read",
            parameters=(
                ApiParameterDoc(
                    name="limit",
                    location="query",
                    required=False,
                    schema_type="integer",
                    description="Maximum number of runs to return.",
                    default=20,
                    example=10,
                ),
                ApiParameterDoc(
                    name="offset",
                    location="query",
                    required=False,
                    schema_type="integer",
                    description="Zero-based offset into the saved run list.",
                    default=0,
                    example=0,
                ),
            ),
            responses=(
                ApiResponseDoc(
                    status_code=200,
                    description="Paginated run summary list.",
                    schema={
                        "type": "object",
                        "properties": {
                            "runs": {"type": "array", "items": _RUN_SUMMARY_SCHEMA},
                            "total": {"type": "integer"},
                            "limit": {"type": "integer"},
                            "offset": {"type": "integer"},
                        },
                        "required": ["runs", "total", "limit", "offset"],
                    },
                    example={
                        "runs": [
                            {
                                "id": SAMPLE_RUN_ID,
                                "event_title": "Fed decision in March",
                                "event_slug": "fed-decision-in-march-885",
                                "source_url": "https://polymarket.com/event/fed-decision-in-march-885",
                                "mode": "resolved-backend",
                                "status": "completed",
                                "graph_stats": {"nodes": 14, "edges": 19, "related_markets": 5},
                                "created_at": "2026-03-13T18:25:44+00:00",
                                "updated_at": "2026-03-13T18:25:44+00:00",
                                "detail_url": f"/api/v1/runs/{SAMPLE_RUN_ID}/",
                                "brief_url": f"/briefs/{SAMPLE_RUN_ID}/",
                                "related_markets_url": f"/api/v1/runs/{SAMPLE_RUN_ID}/related-markets/",
                                "changes_url": f"/api/v1/runs/{SAMPLE_RUN_ID}/changes/",
                            }
                        ],
                        "total": 48,
                        "limit": 10,
                        "offset": 0,
                    },
                ),
            ),
        ),
        ApiEndpointDoc(
            name="Run detail",
            tag="Graph Runs",
            method="GET",
            path="/api/v1/runs/{run_id}/",
            route_name="web:graph_run_detail",
            summary="Load the full persisted graph run.",
            description=(
                "Returns the saved run record including the stored payload, source snapshot, "
                "graph stats, workflow log, and shareable URLs."
            ),
            rate_limit_tier="api_read",
            parameters=(
                ApiParameterDoc(
                    name="run_id",
                    location="path",
                    required=True,
                    schema_type="string",
                    description="Persisted graph run UUID.",
                    example=SAMPLE_RUN_ID,
                ),
            ),
            sample_path_values={"run_id": SAMPLE_RUN_ID},
            responses=(
                ApiResponseDoc(
                    status_code=200,
                    description="Graph run detail payload.",
                    schema={
                        "type": "object",
                        "properties": {
                            "id": {"type": "string", "format": "uuid"},
                            "status": {"type": "string"},
                            "mode": {"type": "string"},
                            "event_title": {"type": "string"},
                            "source_snapshot": {"type": "object"},
                            "graph_stats": {"type": "object"},
                            "workflow_log": {"type": "array"},
                            "payload": {"type": "object"},
                        },
                        "required": ["id", "status", "mode", "payload"],
                    },
                    example={
                        "id": SAMPLE_RUN_ID,
                        "status": "completed",
                        "mode": "resolved-backend",
                        "event_title": "Fed decision in March",
                        "graph_stats": {"nodes": 14, "edges": 19, "related_markets": 5},
                        "workflow_log": [{"step": "event_resolution", "status": "completed"}],
                        "payload": {"graph": {"nodes": [], "edges": []}},
                    },
                ),
                ApiResponseDoc(
                    status_code=404,
                    description="Run not found.",
                    schema=_ERROR_SCHEMA,
                    example={"error": "Not found."},
                ),
            ),
        ),
        ApiEndpointDoc(
            name="Run brief",
            tag="Graph Runs",
            method="GET",
            path="/api/v1/runs/{run_id}/brief/",
            route_name="web:graph_run_brief",
            summary="Return the structured market brief for one saved run.",
            description=(
                "Builds the same brief used by the shareable brief page, including overview, "
                "top related markets, change summary, catalyst timeline, and trust signals."
            ),
            rate_limit_tier="api_read",
            parameters=(
                ApiParameterDoc(
                    name="run_id",
                    location="path",
                    required=True,
                    schema_type="string",
                    description="Persisted graph run UUID.",
                    example=SAMPLE_RUN_ID,
                ),
            ),
            sample_path_values={"run_id": SAMPLE_RUN_ID},
            responses=(
                ApiResponseDoc(
                    status_code=200,
                    description="Structured brief payload.",
                    schema={
                        "type": "object",
                        "properties": {
                            "id": {"type": "string", "format": "uuid"},
                            "brief_url": {"type": "string"},
                            "brief": {"type": "object"},
                        },
                        "required": ["id", "brief"],
                    },
                    example={
                        "id": SAMPLE_RUN_ID,
                        "brief_url": f"/briefs/{SAMPLE_RUN_ID}/",
                        "brief": {
                            "event": {"title": "Fed decision in March"},
                            "top_related_markets": [{"title": "How many Fed rate cuts in 2026?", "confidence": 0.79}],
                            "change_summary": {"headline": "No prior run for comparison."},
                            "catalyst_timeline": [{"label": "FOMC meeting", "confidence": 0.74}],
                            "trust": {"trace_summary": {"stages": [{"stage": "planner", "status": "completed"}]}},
                        },
                    },
                ),
            ),
        ),
        ApiEndpointDoc(
            name="Related-market ranking",
            tag="Graph Runs",
            method="GET",
            path="/api/v1/runs/{run_id}/related-markets/",
            route_name="web:graph_run_related_markets",
            summary="Return the full related-market ranking for one run.",
            description="Returns every ranked related market candidate used in the market brief.",
            rate_limit_tier="api_read",
            parameters=(
                ApiParameterDoc(
                    name="run_id",
                    location="path",
                    required=True,
                    schema_type="string",
                    description="Persisted graph run UUID.",
                    example=SAMPLE_RUN_ID,
                ),
            ),
            sample_path_values={"run_id": SAMPLE_RUN_ID},
            responses=(
                ApiResponseDoc(
                    status_code=200,
                    description="Related-market ranking payload.",
                    schema={
                        "type": "object",
                        "properties": {
                            "id": {"type": "string", "format": "uuid"},
                            "event_title": {"type": "string"},
                            "ranking": {"type": "array", "items": {"type": "object"}},
                            "count": {"type": "integer"},
                        },
                        "required": ["id", "ranking", "count"],
                    },
                    example={
                        "id": SAMPLE_RUN_ID,
                        "event_title": "Fed decision in March",
                        "count": 3,
                        "ranking": [
                            {
                                "rank": 1,
                                "title": "How many Fed rate cuts in 2026?",
                                "confidence": 0.79,
                                "source_url": "https://polymarket.com/event/how-many-fed-rate-cuts-in-2026",
                            }
                        ],
                    },
                ),
            ),
        ),
        ApiEndpointDoc(
            name="Run changes",
            tag="Graph Runs",
            method="GET",
            path="/api/v1/runs/{run_id}/changes/",
            route_name="web:graph_run_changes",
            summary="Summarize what changed versus the prior saved run.",
            description=(
                "Returns the run-to-run difference summary used by the brief page to explain "
                "what moved, what was added, and what evidence changed."
            ),
            rate_limit_tier="api_read",
            parameters=(
                ApiParameterDoc(
                    name="run_id",
                    location="path",
                    required=True,
                    schema_type="string",
                    description="Persisted graph run UUID.",
                    example=SAMPLE_RUN_ID,
                ),
            ),
            sample_path_values={"run_id": SAMPLE_RUN_ID},
            responses=(
                ApiResponseDoc(
                    status_code=200,
                    description="Run-to-run change summary.",
                    schema={
                        "type": "object",
                        "properties": {
                            "id": {"type": "string", "format": "uuid"},
                            "changes": {"type": "object"},
                        },
                        "required": ["id", "changes"],
                    },
                    example={
                        "id": SAMPLE_RUN_ID,
                        "changes": {
                            "headline": "Related market drift increased in rates-linked contracts.",
                            "new_nodes": 2,
                            "new_edges": 3,
                        },
                    },
                ),
            ),
        ),
        ApiEndpointDoc(
            name="Review saved run",
            tag="Graph Runs",
            method="POST",
            path="/api/v1/runs/{run_id}/review/",
            route_name="web:review_graph_run",
            summary="Trigger a review pass for a saved run.",
            description=(
                "Reruns the review layer against an existing run. When Anthropic is enabled, "
                "the response can include LLM-backed review details; otherwise it returns the deterministic review."
            ),
            rate_limit_tier="api_write",
            parameters=(
                ApiParameterDoc(
                    name="run_id",
                    location="path",
                    required=True,
                    schema_type="string",
                    description="Persisted graph run UUID.",
                    example=SAMPLE_RUN_ID,
                ),
            ),
            sample_path_values={"run_id": SAMPLE_RUN_ID},
            responses=(
                ApiResponseDoc(
                    status_code=200,
                    description="Run review completed.",
                    schema={
                        "type": "object",
                        "properties": {
                            "id": {"type": "string", "format": "uuid"},
                            "review": {"type": "object"},
                            "mode": {"type": "string"},
                            "model_name": {"type": "string"},
                        },
                        "required": ["id", "review"],
                    },
                    example={
                        "id": SAMPLE_RUN_ID,
                        "mode": "resolved-backend",
                        "model_name": "",
                        "review": {
                            "approved": True,
                            "quality_score": 0.82,
                            "issues": [],
                            "follow_up_actions": ["Monitor rate-cut market drift."],
                        },
                    },
                ),
                ApiResponseDoc(
                    status_code=400,
                    description="Review could not be run for that saved run.",
                    schema=_ERROR_SCHEMA,
                    example={"error": "Saved run payload is missing reviewable graph data."},
                ),
            ),
        ),
        ApiEndpointDoc(
            name="Benchmark summary",
            tag="Benchmarks",
            method="GET",
            path="/api/v1/benchmarks/summary/",
            route_name="web:benchmark_summary",
            summary="Return the current benchmark dashboard snapshot.",
            description=(
                "Returns the cached benchmark summary that powers the public benchmarks page, "
                "including live tracks, experiment runs, mode breakdown, and human review status."
            ),
            rate_limit_tier="api_read",
            responses=(
                ApiResponseDoc(
                    status_code=200,
                    description="Benchmark dashboard payload.",
                    schema={
                        "type": "object",
                        "properties": {
                            "summary_cards": {"type": "array", "items": {"type": "object"}},
                            "live_benchmarks": {"type": "array", "items": {"type": "object"}},
                            "next_benchmarks": {"type": "array", "items": {"type": "object"}},
                            "experiment_runs": {"type": "array", "items": {"type": "object"}},
                            "human_label_review": {"type": "object"},
                        },
                        "required": ["summary_cards", "live_benchmarks", "experiment_runs"],
                    },
                    example={
                        "summary_cards": [{"label": "Runs", "value": "48", "copy": "Saved graph runs in the benchmark dataset."}],
                        "live_benchmarks": [{"name": "Agent instrumentation coverage", "primary_metric": "100% run coverage"}],
                        "experiment_runs": [{"task_type": "agent_eval", "title": "Agent instrumentation coverage"}],
                        "human_label_review": {"judgment_count": 0, "pending_cases": 12, "avg_agreement_rate": 0.0},
                    },
                ),
            ),
        ),
        ApiEndpointDoc(
            name="Related-market review queue",
            tag="Benchmarks",
            method="GET",
            path="/api/v1/benchmarks/related-market-review/",
            route_name="web:related_market_review_queue",
            summary="Load the human review queue for related-market usefulness.",
            description=(
                "Returns recent related-market candidates, reviewer-aware consensus state, "
                "and pending/contested review counts."
            ),
            rate_limit_tier="api_read",
            responses=(
                ApiResponseDoc(
                    status_code=200,
                    description="Human review queue payload.",
                    schema={
                        "type": "object",
                        "properties": {
                            "cases": {"type": "array", "items": {"type": "object"}},
                            "summary": {"type": "object"},
                        },
                        "required": ["cases", "summary"],
                    },
                    example={
                        "summary": {
                            "judgment_count": 0,
                            "pending_cases": 12,
                            "contested_candidates": 0,
                            "avg_agreement_rate": 0.0,
                        },
                        "cases": [
                            {
                                "run_id": SAMPLE_RUN_ID,
                                "event_title": "Fed decision in March",
                                "candidates": [
                                    {
                                        "candidate_key": "how-many-fed-rate-cuts-in-2026",
                                        "candidate_title": "How many Fed rate cuts in 2026?",
                                        "review_state": "pending",
                                    }
                                ],
                            }
                        ],
                    },
                ),
            ),
        ),
        ApiEndpointDoc(
            name="Submit related-market judgment",
            tag="Benchmarks",
            method="POST",
            path="/api/v1/benchmarks/related-market-review/submit/",
            route_name="web:submit_related_market_judgment",
            summary="Persist a human usefulness judgment for one related-market candidate.",
            description=(
                "Records or updates a reviewer-scoped usefulness label for one related-market candidate. "
                "Consensus is computed across reviewer keys rather than by destructive overwrite."
            ),
            rate_limit_tier="api_write",
            request_body=ApiRequestBodyDoc(
                description="Human judgment payload.",
                fields=(
                    ApiFieldDoc("run_id", "string", "Persisted graph run UUID.", required=True, example=SAMPLE_RUN_ID),
                    ApiFieldDoc("candidate_key", "string", "Stable key for the ranked related-market candidate.", required=True, example="how-many-fed-rate-cuts-in-2026"),
                    ApiFieldDoc("candidate_title", "string", "Human-readable title for the candidate.", required=True, example="How many Fed rate cuts in 2026?"),
                    ApiFieldDoc("usefulness_label", "string", "Reviewer label for trader usefulness.", required=True, example="useful"),
                    ApiFieldDoc("notes", "string", "Optional reviewer notes.", example="Directly reprices alongside the source market."),
                    ApiFieldDoc("reviewer", "string", "Reviewer handle used to derive reviewer_key.", required=True, example="caesar"),
                    ApiFieldDoc("candidate_summary", "string", "Optional candidate summary shown in the review queue.", example="Rates-linked contract with clear spillover."),
                    ApiFieldDoc("candidate_source_url", "string", "Optional market URL for the candidate.", example="https://polymarket.com/event/how-many-fed-rate-cuts-in-2026"),
                    ApiFieldDoc("candidate_rank", "integer", "Ranking position at the time of review.", example=1),
                    ApiFieldDoc("candidate_confidence", "number", "Model confidence for the candidate.", example=0.79),
                ),
                example={
                    "run_id": SAMPLE_RUN_ID,
                    "candidate_key": "how-many-fed-rate-cuts-in-2026",
                    "candidate_title": "How many Fed rate cuts in 2026?",
                    "usefulness_label": "useful",
                    "notes": "Directly reprices alongside the source market.",
                    "reviewer": "caesar",
                    "candidate_summary": "Rates-linked contract with clear spillover.",
                    "candidate_source_url": "https://polymarket.com/event/how-many-fed-rate-cuts-in-2026",
                    "candidate_rank": 1,
                    "candidate_confidence": 0.79,
                },
            ),
            responses=(
                ApiResponseDoc(
                    status_code=200,
                    description="Judgment persisted successfully.",
                    schema={
                        "type": "object",
                        "properties": {
                            "status": {"type": "string"},
                            "judgment": {"type": "object"},
                        },
                        "required": ["status", "judgment"],
                    },
                    example={
                        "status": "ok",
                        "judgment": {
                            "id": 14,
                            "run_id": SAMPLE_RUN_ID,
                            "candidate_key": "how-many-fed-rate-cuts-in-2026",
                            "candidate_title": "How many Fed rate cuts in 2026?",
                            "usefulness_label": "useful",
                            "reviewer": "caesar",
                            "reviewer_key": "caesar",
                            "updated_at": "2026-03-13T20:20:11+00:00",
                        },
                    },
                ),
                ApiResponseDoc(
                    status_code=400,
                    description="Validation failed for the submitted label.",
                    schema=_ERROR_SCHEMA,
                    example={"error": "The `run_id` field is required."},
                ),
            ),
        ),
        ApiEndpointDoc(
            name="Lead-lag summary",
            tag="Lead-Lag",
            method="GET",
            path="/api/v1/lead-lag/summary/",
            route_name="web:leadlag_summary",
            summary="Return the cached lead-lag monitor snapshot.",
            description=(
                "Returns the current cross-venue research summary, including coverage status, "
                "pair diagnostics, recent signals, and paper-trade statistics."
            ),
            rate_limit_tier="api_read",
            responses=(
                ApiResponseDoc(
                    status_code=200,
                    description="Lead-lag monitor summary.",
                    schema={
                        "type": "object",
                        "properties": {
                            "coverage_status": {"type": "string"},
                            "coverage_summary": {"type": "string"},
                            "totals": {"type": "object"},
                            "pair_diagnostics": {"type": "array", "items": {"type": "object"}},
                            "recent_signals": {"type": "array", "items": {"type": "object"}},
                        },
                        "required": ["coverage_status", "totals", "pair_diagnostics", "recent_signals"],
                    },
                    example={
                        "coverage_status": "watch_only",
                        "coverage_summary": "One plausible cross-venue pair exists, but history is still too thin for trade eligibility.",
                        "totals": {"active_pairs": 1, "trade_eligible_pairs": 0, "recent_signals": 2},
                        "pair_diagnostics": [{"pair_id": SAMPLE_PAIR_ID, "leader_title": "Tim Walz 2028", "readiness_status": "watch_only"}],
                        "recent_signals": [{"pair_id": SAMPLE_PAIR_ID, "status": "no_trade", "expected_edge": 0.0}],
                    },
                ),
            ),
        ),
        ApiEndpointDoc(
            name="Lead-lag signals",
            tag="Lead-Lag",
            method="GET",
            path="/api/v1/lead-lag/signals/",
            route_name="web:leadlag_signals",
            summary="Return recent candidate and no-trade signals.",
            description="Returns the recent lead-lag signals along with the top-level signal counts.",
            rate_limit_tier="api_read",
            responses=(
                ApiResponseDoc(
                    status_code=200,
                    description="Recent lead-lag signals.",
                    schema={
                        "type": "object",
                        "properties": {
                            "signals": {"type": "array", "items": {"type": "object"}},
                            "count": {"type": "integer"},
                            "totals": {"type": "object"},
                        },
                        "required": ["signals", "count", "totals"],
                    },
                    example={
                        "signals": [{"pair_id": SAMPLE_PAIR_ID, "status": "no_trade", "signal_direction": "leader_up"}],
                        "count": 1,
                        "totals": {"candidate_signals": 0, "no_trade_signals": 1},
                    },
                ),
            ),
        ),
        ApiEndpointDoc(
            name="Lead-lag pairs",
            tag="Lead-Lag",
            method="GET",
            path="/api/v1/lead-lag/pairs/",
            route_name="web:leadlag_pairs",
            summary="Return scored cross-venue lead-lag pairs.",
            description="Returns the current active pair registry used by the lead-lag monitor and paper-trade research flow.",
            rate_limit_tier="api_read",
            responses=(
                ApiResponseDoc(
                    status_code=200,
                    description="Active lead-lag pairs.",
                    schema={
                        "type": "object",
                        "properties": {
                            "pairs": {"type": "array", "items": {"type": "object"}},
                            "count": {"type": "integer"},
                        },
                        "required": ["pairs", "count"],
                    },
                    example={
                        "pairs": [
                            {
                                "pair_id": SAMPLE_PAIR_ID,
                                "leader_title": "Tim Walz 2028",
                                "follower_title": "Tim Walz",
                                "readiness_status": "watch_only",
                                "is_trade_eligible": False,
                            }
                        ],
                        "count": 1,
                    },
                ),
            ),
        ),
        ApiEndpointDoc(
            name="Lead-lag pair detail",
            tag="Lead-Lag",
            method="GET",
            path="/api/v1/lead-lag/pairs/{pair_id}/",
            route_name="web:leadlag_pair_detail",
            summary="Return diagnostics, signals, and paper trades for one pair.",
            description=(
                "Returns the full pair-level view used by the monitor, including score breakdowns, "
                "readiness metadata, recent signals, and recent paper trades."
            ),
            rate_limit_tier="api_read",
            parameters=(
                ApiParameterDoc(
                    name="pair_id",
                    location="path",
                    required=True,
                    schema_type="integer",
                    description="Lead-lag pair identifier.",
                    example=SAMPLE_PAIR_ID,
                ),
            ),
            sample_path_values={"pair_id": SAMPLE_PAIR_ID},
            responses=(
                ApiResponseDoc(
                    status_code=200,
                    description="Detailed lead-lag pair diagnostics.",
                    schema={
                        "type": "object",
                        "properties": {
                            "id": {"type": "integer"},
                            "pair_type": {"type": "string"},
                            "leader_market": {"type": "object"},
                            "follower_market": {"type": "object"},
                            "scores": {"type": "object"},
                            "readiness": {"type": "object"},
                            "signals": {"type": "array", "items": {"type": "object"}},
                            "paper_trades": {"type": "array", "items": {"type": "object"}},
                        },
                        "required": ["id", "leader_market", "follower_market", "scores", "readiness"],
                    },
                    example={
                        "id": SAMPLE_PAIR_ID,
                        "pair_type": "logical-equivalent",
                        "leader_market": {"venue": "polymarket", "title": "Tim Walz 2028", "url": "https://polymarket.com/event/tim-walz-2028"},
                        "follower_market": {"venue": "kalshi", "title": "Tim Walz", "url": "https://kalshi.com/markets/tim-walz"},
                        "scores": {"semantic_score": 0.92, "causal_score": 0.61, "resolution_score": 0.84, "stability_score": 0.34, "composite_score": 0.68},
                        "readiness": {"status": "watch_only", "stability_samples": 5, "move_samples": 2, "leader_first_ratio": 0.5, "avg_lead_seconds": 7.2},
                        "signals": [],
                        "paper_trades": [],
                    },
                ),
                ApiResponseDoc(
                    status_code=404,
                    description="Pair not found or no longer active.",
                    schema=_ERROR_SCHEMA,
                    example={"error": "Not found."},
                ),
            ),
        ),
        ApiEndpointDoc(
            name="Trending markets",
            tag="Markets",
            method="GET",
            path="/api/v1/markets/trending/",
            route_name="web:trending_markets",
            summary="Return trending Polymarket events.",
            description="Returns the top Polymarket events ranked by 24-hour volume using the public Gamma API.",
            rate_limit_tier="api_read",
            parameters=(
                ApiParameterDoc(
                    name="limit",
                    location="query",
                    required=False,
                    schema_type="integer",
                    description="Maximum number of trending events to return.",
                    default=6,
                    example=6,
                ),
            ),
            responses=(
                ApiResponseDoc(
                    status_code=200,
                    description="Trending Polymarket events.",
                    schema={
                        "type": "object",
                        "properties": {
                            "markets": {"type": "array", "items": {"type": "object"}},
                            "count": {"type": "integer"},
                            "source": {"type": "string"},
                        },
                        "required": ["markets", "count", "source"],
                    },
                    example={
                        "markets": [
                            {
                                "title": "Fed decision in March",
                                "source_url": "https://polymarket.com/event/fed-decision-in-march-885",
                                "volume": 152000.0,
                            }
                        ],
                        "count": 1,
                        "source": "polymarket-gamma-api",
                    },
                ),
            ),
        ),
        ApiEndpointDoc(
            name="Verify market links",
            tag="Markets",
            method="POST",
            path="/api/v1/markets/verify/",
            route_name="web:verify_links",
            summary="Verify the reachability of a batch of market links.",
            description="Checks up to 20 URLs and returns a reachability map so briefs can surface broken links honestly.",
            rate_limit_tier="api_write",
            request_body=ApiRequestBodyDoc(
                description="Batch of URLs to verify.",
                fields=(
                    ApiFieldDoc(
                        name="urls",
                        field_type="array",
                        description="List of URLs to verify, up to 20 entries.",
                        required=True,
                        example=[
                            "https://polymarket.com/event/fed-decision-in-march-885",
                            "https://polymarket.com/event/how-many-fed-rate-cuts-in-2026",
                        ],
                    ),
                ),
                example={
                    "urls": [
                        "https://polymarket.com/event/fed-decision-in-march-885",
                        "https://polymarket.com/event/how-many-fed-rate-cuts-in-2026",
                    ]
                },
            ),
            responses=(
                ApiResponseDoc(
                    status_code=200,
                    description="Link verification results.",
                    schema={
                        "type": "object",
                        "properties": {
                            "results": {
                                "type": "object",
                                "additionalProperties": {"type": "boolean"},
                            }
                        },
                        "required": ["results"],
                    },
                    example={
                        "results": {
                            "https://polymarket.com/event/fed-decision-in-march-885": True,
                            "https://polymarket.com/event/how-many-fed-rate-cuts-in-2026": True,
                        }
                    },
                ),
                ApiResponseDoc(
                    status_code=400,
                    description="Invalid verification request.",
                    schema=_ERROR_SCHEMA,
                    example={"error": "Provide a list of up to 20 URLs."},
                ),
            ),
        ),
        ApiEndpointDoc(
            name="Watchlists",
            tag="Watchlists",
            method="GET",
            path="/api/v1/watchlists/",
            route_name="web:watchlists_api",
            summary="Return the public watchlist catalog.",
            description="Returns the current curated trader watchlists used by the app and landing surfaces.",
            rate_limit_tier="api_read",
            responses=(
                ApiResponseDoc(
                    status_code=200,
                    description="Watchlist catalog.",
                    schema={
                        "type": "object",
                        "properties": {
                            "watchlists": {"type": "array", "items": {"type": "object"}},
                            "count": {"type": "integer"},
                        },
                        "required": ["watchlists", "count"],
                    },
                    example={
                        "watchlists": [
                            {
                                "slug": "macro-rates",
                                "title": "Macro rates",
                                "thesis": "Start from Fed and CPI markets, then follow rate-sensitive contracts.",
                                "markets": [{"title": "Fed decision in March"}],
                            }
                        ],
                        "count": 1,
                    },
                ),
            ),
        ),
    )


class ApiReferenceService:
    def __init__(self) -> None:
        self._endpoints = _endpoint_docs()

    @property
    def endpoints(self) -> tuple[ApiEndpointDoc, ...]:
        return self._endpoints

    def base_url(self, request) -> str:
        return request.build_absolute_uri("/").rstrip("/")

    def docs_url(self) -> str:
        return reverse("web:api_docs")

    def openapi_url(self) -> str:
        return reverse("web:openapi_spec")

    def api_root_url(self) -> str:
        return reverse("web:api_root")

    def common_errors(self) -> list[dict[str, Any]]:
        errors = [
            {
                "status": 400,
                "label": "Bad request",
                "copy": "Validation failed or the request body was malformed.",
                "example": {"error": "Request body must be valid JSON."},
            },
            {
                "status": 404,
                "label": "Not found",
                "copy": "The requested run or pair does not exist.",
                "example": {"error": "Not found."},
            },
            {
                "status": 413,
                "label": "Request too large",
                "copy": (
                    f"Request bodies larger than "
                    f"{_format_bytes(settings.CHAOSWING_MAX_REQUEST_BODY_BYTES)} are rejected."
                ),
                "example": {"error": "Request body too large."},
            },
            {
                "status": 429,
                "label": "Rate limited",
                "copy": "Production rate limiting blocked the caller or burst protection tripped.",
                "example": {"error": "Rate limit exceeded. Try again in 60s."},
            },
            {
                "status": 500,
                "label": "Server error",
                "copy": "ChaosWing failed to complete the request.",
                "example": {"error": "ChaosWing could not generate a graph run."},
            },
        ]
        for error in errors:
            error["example_json"] = json.dumps(error["example"], indent=2)
        return errors

    def build_api_index(self, request) -> dict[str, Any]:
        return {
            "name": API_REFERENCE_TITLE,
            "version": API_REFERENCE_VERSION,
            "docs_url": self.docs_url(),
            "openapi_url": self.openapi_url(),
            "base_url": self.base_url(request),
            "authentication": {
                "mode": "none",
                "copy": (
                    "The public API is currently unauthenticated. Production rate limits still apply."
                ),
            },
            "rate_limits": _rate_limit_profile(),
            "resources": [
                {
                    "tag": endpoint.tag,
                    "method": endpoint.method,
                    "path": endpoint.path,
                    "summary": endpoint.summary,
                    "rate_limit_tier": endpoint.rate_limit_tier,
                }
                for endpoint in self.endpoints
            ],
        }

    def build_openapi(self, request) -> dict[str, Any]:
        base_url = self.base_url(request)
        paths: dict[str, Any] = {}
        for endpoint in self.endpoints:
            paths.setdefault(endpoint.path, {})
            paths[endpoint.path][endpoint.method.lower()] = endpoint.to_openapi(base_url)

        return {
            "openapi": OPENAPI_VERSION,
            "info": {
                "title": API_REFERENCE_TITLE,
                "version": API_REFERENCE_VERSION,
                "summary": (
                    "Public API for ChaosWing market briefs, graph runs, benchmarks, and lead-lag research."
                ),
                "description": (
                    "ChaosWing exposes a public JSON API for generating trader-facing market "
                    "intelligence from Polymarket URLs, loading saved runs, reading benchmark "
                    "output, and inspecting cross-venue lead-lag research state."
                ),
            },
            "servers": [{"url": base_url}],
            "externalDocs": {
                "description": "Human-readable API reference",
                "url": f"{base_url}{self.docs_url()}",
            },
            "tags": list(_TAGS),
            "paths": paths,
            "components": {
                "schemas": {
                    "ErrorResponse": _ERROR_SCHEMA,
                    "RunSummary": _RUN_SUMMARY_SCHEMA,
                }
            },
        }

    def build_docs_context(self, request) -> dict[str, Any]:
        base_url = self.base_url(request)
        grouped_endpoints: dict[str, list[dict[str, Any]]] = {tag["name"]: [] for tag in _TAGS}
        anchor_by_route = {endpoint.route_name: endpoint.anchor for endpoint in self.endpoints}
        related_routes = {
            "web:graph_from_url": ["web:graph_run_detail", "web:graph_run_brief", "web:graph_run_related_markets"],
            "web:list_graph_runs": ["web:graph_run_detail", "web:graph_run_brief"],
            "web:graph_run_detail": ["web:graph_run_brief", "web:review_graph_run", "web:graph_run_changes"],
            "web:graph_run_brief": ["web:graph_run_related_markets", "web:graph_run_changes"],
            "web:benchmark_summary": ["web:related_market_review_queue", "web:submit_related_market_judgment"],
            "web:leadlag_summary": ["web:leadlag_signals", "web:leadlag_pairs"],
            "web:leadlag_pairs": ["web:leadlag_pair_detail"],
            "web:trending_markets": ["web:graph_from_url", "web:watchlists_api"],
        }

        for endpoint in self.endpoints:
            response_fields = []
            for response in endpoint.responses:
                if response.status_code == 200:
                    response_fields = _schema_field_rows(response.schema)
                    break
            grouped_endpoints[endpoint.tag].append(
                {
                    "name": endpoint.name,
                    "summary": endpoint.summary,
                    "description": endpoint.description,
                    "method": endpoint.method,
                    "method_class": endpoint.method.lower(),
                    "path": endpoint.path,
                    "anchor": endpoint.anchor,
                    "notes": endpoint.notes,
                    "rate_limit_tier": endpoint.rate_limit_tier.replace("_", " "),
                    "facts": [
                        {"label": "Method", "value": endpoint.method},
                        {"label": "Path", "value": endpoint.path},
                        {"label": "Auth", "value": "None"},
                        {"label": "Format", "value": "application/json"},
                        {"label": "Version", "value": API_REFERENCE_VERSION},
                    ],
                    "parameters": [
                        {
                            "name": parameter.name,
                            "location": parameter.location,
                            "required": parameter.required,
                            "type": parameter.schema_type,
                            "description": parameter.description,
                            "example": parameter.example,
                            "default": parameter.default,
                        }
                        for parameter in endpoint.parameters
                    ],
                    "request_body": (
                        {
                            "description": endpoint.request_body.description,
                            "fields": [
                                {
                                    "name": field.name,
                                    "required": field.required,
                                    "type": field.field_type,
                                    "description": field.description,
                                    "example": field.example,
                                }
                                for field in endpoint.request_body.fields
                            ],
                            "example": json.dumps(endpoint.request_body.example, indent=2),
                        }
                        if endpoint.request_body
                        else None
                    ),
                    "response_fields": response_fields,
                    "responses": [
                        {
                            "status_code": response.status_code,
                            "description": response.description,
                            "example": (
                                json.dumps(response.example, indent=2)
                                if response.example is not None
                                else ""
                            ),
                        }
                        for response in endpoint.responses
                    ],
                    "related_operations": [
                        {
                            "name": next_endpoint.name,
                            "summary": next_endpoint.summary,
                            "href": f"#{next_endpoint.anchor}",
                        }
                        for next_endpoint in self.endpoints
                        if next_endpoint.route_name in related_routes.get(endpoint.route_name, [])
                    ],
                    "curl_sample": endpoint.curl_sample(base_url),
                    "fetch_sample": endpoint.fetch_sample(base_url),
                }
            )

        sections = [
            {
                "name": tag["name"],
                "description": tag["description"],
                "anchor": slugify(tag["name"]),
                "endpoints": grouped_endpoints[tag["name"]],
            }
            for tag in _TAGS
        ]

        return {
            "title": API_REFERENCE_TITLE,
            "version": API_REFERENCE_VERSION,
            "base_url": base_url,
            "docs_url": self.docs_url(),
            "openapi_url": self.openapi_url(),
            "api_root_url": self.api_root_url(),
            "nav_sections": [
                {"name": tag["name"], "anchor": slugify(tag["name"])} for tag in _TAGS
            ],
            "sections": sections,
            "common_errors": self.common_errors(),
            "rate_limits": _rate_limit_profile(),
            "overview_cards": [
                {
                    "label": "Base URL",
                    "value": base_url,
                    "copy": "All public JSON endpoints live under the same host as the app.",
                },
                {
                    "label": "Versioning",
                    "value": "/api/v1/",
                    "copy": (
                        "ChaosWing versions the public API in the URL path and echoes that version in response headers."
                    ),
                },
                {
                    "label": "Authentication",
                    "value": "Public beta",
                    "copy": (
                        "The current public API is unauthenticated. Use server-side clients and respect the documented rate limits."
                    ),
                },
                {
                    "label": "Request body limit",
                    "value": _format_bytes(settings.CHAOSWING_MAX_REQUEST_BODY_BYTES),
                    "copy": "Large write requests are rejected before they hit the graph workflow.",
                },
            ],
            "workflows": [
                {
                    "title": "Generate a market brief",
                    "copy": (
                        "Start from one Polymarket URL, persist the run, then load the brief and "
                        "related-market ranking for presentation or downstream analysis."
                    ),
                    "steps": [
                        {"label": "Generate run", "href": f"#{anchor_by_route['web:graph_from_url']}"},
                        {"label": "Load brief", "href": f"#{anchor_by_route['web:graph_run_brief']}"},
                        {"label": "Inspect related markets", "href": f"#{anchor_by_route['web:graph_run_related_markets']}"},
                    ],
                },
                {
                    "title": "Review ranking quality",
                    "copy": (
                        "Read the benchmark snapshot, inspect the related-market review queue, then "
                        "submit human usefulness judgments that can promote the ranking benchmark to ground truth."
                    ),
                    "steps": [
                        {"label": "Read benchmarks", "href": f"#{anchor_by_route['web:benchmark_summary']}"},
                        {"label": "Load review queue", "href": f"#{anchor_by_route['web:related_market_review_queue']}"},
                        {"label": "Submit judgment", "href": f"#{anchor_by_route['web:submit_related_market_judgment']}"},
                    ],
                },
                {
                    "title": "Monitor lead-lag research",
                    "copy": (
                        "Fetch the lead-lag summary first, then drill into recent signals and detailed "
                        "pair diagnostics to decide whether a candidate stays watch-only or becomes actionable research."
                    ),
                    "steps": [
                        {"label": "Read monitor summary", "href": f"#{anchor_by_route['web:leadlag_summary']}"},
                        {"label": "Inspect signals", "href": f"#{anchor_by_route['web:leadlag_signals']}"},
                        {"label": "Open pair detail", "href": f"#{anchor_by_route['web:leadlag_pair_detail']}"},
                    ],
                },
            ],
            "protocol_notes": [
                "JSON is the canonical format for all public endpoints.",
                "Public POST endpoints are CSRF-exempt so server-to-server clients can call them directly.",
                "Every API response includes X-ChaosWing-Api-Version plus Link headers to the docs page and OpenAPI description.",
                "Production rate limits are enforced by middleware and are disabled in local debug mode.",
            ],
        }
