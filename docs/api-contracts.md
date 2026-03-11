# API Contracts

ChaosWing currently exposes one graph-generation endpoint, two saved-run endpoints, and three inspector partial endpoints.

## Graph Generation

`POST /api/v1/graph/from-url/`

### Request

```json
{
  "url": "https://polymarket.com/event/will-brent-crude-trade-above-95-before-july-2026"
}
```

### Response Shape

```json
{
  "event": {
    "id": "evt_001",
    "title": "Will Brent crude trade above $95 before July 2026?",
    "source_url": "https://polymarket.com/event/will-brent-crude-trade-above-95-before-july-2026",
    "description": "Polymarket event under analysis...",
    "status": "open",
    "tags": ["energy", "macro", "oil", "shipping"],
    "outcomes": ["Yes", "No"],
    "updated_at": "2026-03-10T18:00:00Z"
  },
  "run": {
    "id": "uuid",
    "mode": "resolved-backend",
    "model_name": "",
    "persistence": "database",
    "graph_stats": {
      "nodes": 12,
      "edges": 16,
      "related_markets": 3,
      "evidence_nodes": 3
    },
    "review": {
      "approved": true,
      "issues": [],
      "follow_up_actions": ["No deterministic structural issues were found."],
      "quality_score": 0.88
    },
    "workflow": [
      {
        "step": "event_resolution",
        "status": "completed",
        "detail": "Resolved source event from gamma-api."
      }
    ]
  },
  "context": {
    "source_snapshot": {},
    "related_candidates": []
  },
  "assets": {
    "event_primary": "data:image/..."
  },
  "graph": {
    "nodes": [
      {
        "id": "evt_001",
        "label": "Will Brent crude trade above $95 before July 2026?",
        "type": "Event",
        "confidence": 1.0,
        "summary": "Primary market event summary.",
        "source_url": "https://polymarket.com/event/will-brent-crude-trade-above-95-before-july-2026",
        "source_title": "Will Brent crude trade above $95 before July 2026?",
        "source_description": "Polymarket event under analysis...",
        "icon_key": "event_primary",
        "metadata": [{ "label": "Category", "value": "Prediction Market" }],
        "evidence_snippets": []
      }
    ],
    "edges": [
      {
        "id": "edge_evt_001_mkt_1_related_to",
        "source": "evt_001",
        "target": "mkt_1_related-contract",
        "type": "related_to",
        "confidence": 0.82,
        "explanation": "Shared tags and title overlap suggest this adjacent contract belongs in the butterfly graph."
      }
    ]
  }
}
```

## Saved Runs

### `GET /api/v1/runs/<uuid>/`

Returns the persisted `GraphRun` record including `source_snapshot`, `graph_stats`, `workflow_log`, and the full stored payload.

### `POST /api/v1/runs/<uuid>/review/`

Runs the review layer against an existing saved run. If Anthropic is configured, the review uses Claude. Otherwise ChaosWing stores and returns a deterministic structural review.

## Inspector Partials

- `GET /partials/inspector/empty/`
- `POST /partials/inspector/node/`
- `POST /partials/inspector/edge/`

The browser posts selection payloads back to Django and Django returns HTML fragments. This keeps node and edge inspection visibly server-rendered.

## Validation Rules

- `url` is required for `POST /api/v1/graph/from-url/`
- the URL must be a full `http` or `https` Polymarket URL
- graph nodes must use valid node types and include sources, summaries, and icon keys
- graph edges must use valid edge types and point to existing nodes
- invalid JSON returns `400`

## Future API Notes

The current contract leaves room for:

- richer evidence provenance
- replay and comparison endpoints
- background graph generation
- calibration and evaluation outputs
- Kalshi or other market adapters alongside Polymarket
