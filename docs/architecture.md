# Architecture

ChaosWing is a Django-driven product shell wrapped around a graph-construction workflow. The browser is rich, but the backend is no longer just a thin mock layer.

## High-Level System Architecture

- `GET /` renders the public landing page
- `GET /app/` renders the dashboard shell and initial inspector partial
- `POST /api/v1/graph/from-url/` resolves a Polymarket event, builds a graph, optionally runs the agent, and persists the run
- `GET /api/v1/runs/<uuid>/` returns a saved graph run
- `POST /api/v1/runs/<uuid>/review/` reruns the review layer on a saved run
- inspector partial routes render empty, node, and edge views through Django templates

## Backend Layers

### Configuration Layer

`chaoswing/config.py` is the single runtime-entry point for environment parsing. Django settings, backend toggles, HTTP timeouts, and Anthropic credentials all flow through that module so public-repository configuration stays explicit and secrets never need to live in source files.

### Web Interface Layer

Django templates own the product shell, initial state, CSRF, and the server-rendered inspector fragments.

### API Layer

`api_views.py` validates incoming event URLs, returns graph payloads, exposes saved runs, and exposes the review surface for persisted runs.

### Event Resolution Layer

`services/polymarket.py` resolves events from Polymarket Gamma, normalizes tags and market metadata, and discovers related contracts from shared tags and narrative overlap. If live resolution fails, the service falls back to deterministic local metadata so the interface still works.

### Graph Construction Layer

`services/graph_builder.py` transforms normalized event data into Event, Entity, RelatedMarket, Evidence, Rule, and Hypothesis nodes plus typed edges. This is the main business-meaning layer.

### Agent Workflow Layer

`services/anthropic_agent.py` defines the optional Claude expansion and review passes. `services/graph_workflow.py` orchestrates the steps in order: resolve, discover, build, enrich, review, validate, and persist.

### Evaluation / Replay Layer

`GraphRun` stores the normalized source snapshot, graph stats, workflow log, and final payload. That gives the system a basic replay surface now and a stronger evaluation seam later.

## Why Django + Vanilla JS + Cytoscape.js

### Django

Django gives ChaosWing visible server ownership: routing, templates, CSRF handling, JSON endpoints, persistence, tests, and management commands.

### Vanilla JS with ES modules

The graph stage is dynamic, but the product is still focused enough that React would add more ceremony than leverage. ES modules keep the graph surface modular without hiding browser behavior.

### Cytoscape.js

Cytoscape.js is the graph engine for layouts, selection behavior, hover states, node imagery, and relayout transitions.

## Future Backend Seam

The current seam is already explicit:

1. `PolymarketMetadataService` can be replaced or expanded with richer market adapters
2. `RelatedMarketDiscoveryService` can be upgraded into a ranking pipeline
3. `GraphConstructionService` can be replaced by a true causal graph builder
4. `AnthropicGraphAgent` can evolve into a fuller agent workflow or LangGraph-based orchestration
5. `GraphRun` can back replay, evaluation, calibration, and analyst feedback loops
