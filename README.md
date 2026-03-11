<div align="center">

<br/>

<img src="docs/assets/logo.svg" width="72" height="72" alt="ChaosWing logo" />

<h1>ChaosWing</h1>

<p><strong>The Butterfly Effect Engine for Prediction Markets</strong></p>

<p>
  Paste one Polymarket URL.<br/>
  ChaosWing resolves the event, maps every causal chain, and surfaces<br/>
  the butterfly effects you never knew existed.
</p>

<br/>

[![Python](https://img.shields.io/badge/Python-3.12-3776AB?style=flat-square&logo=python&logoColor=white)](https://python.org)
[![Django](https://img.shields.io/badge/Django-5.x-092E20?style=flat-square&logo=django&logoColor=white)](https://djangoproject.com)
[![Cytoscape.js](https://img.shields.io/badge/Cytoscape.js-3.30-F7941D?style=flat-square)](https://cytoscape.js.org)
[![Claude](https://img.shields.io/badge/Claude-Sonnet_4.6-CC785C?style=flat-square)](https://anthropic.com)
[![License](https://img.shields.io/badge/license-MIT-blue?style=flat-square)](LICENSE)
[![PRs Welcome](https://img.shields.io/badge/PRs-welcome-brightgreen?style=flat-square)](CONTRIBUTING.md)

<br/>

[**Launch App**](http://127.0.0.1:8000/) Â· [**Quick Start**](#quick-start) Â· [**Architecture**](#architecture) Â· [**Contribute**](CONTRIBUTING.md)

<br/>

</div>

---

## What is ChaosWing?

Prediction markets don't exist in isolation. Every contract is connected to dozens of others through shared entities, common evidence, overlapping rules, and second-order causal chains. **ChaosWing makes those connections visible.**

Paste a single [Polymarket](https://polymarket.com) event URL. Within seconds, ChaosWing:

1. **Resolves** the live event from the Polymarket Gamma API â€” real probabilities, real metadata, real outcomes
2. **Discovers** adjacent markets sharing tags, entities, and narrative overlap
3. **Constructs** a butterfly graph: Event at the center, rippling outward through Entities, Evidence, Rules, Related Markets, and Hypotheses
4. **Optionally expands** the graph using a Claude-powered analyst pass that adds high-value causal nodes and refines edge explanations
5. **Persists** every run as a `GraphRun` record â€” replay any analysis instantly, trigger re-reviews, compare runs

The result is a live, interactive graph workspace that lets you trace the exact butterfly effect of any prediction market event â€” from the central contract all the way out to second-order impact paths you'd never find manually.

---

## The Graph in 60 Seconds

```
                              â”Œâ”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”
                              â”‚   EVIDENCE       â”‚  â† Real market data,
                              â”‚  "CPI prints     â”‚    source descriptions,
                              â”‚   above 3.2%"    â”‚    linked URLs
                              â””â”€â”€â”€â”€â”€â”€â”€â”€â”¬â”€â”€â”€â”€â”€â”€â”€â”€â”€â”˜
                                       â”‚ supported_by
                              â•”â•â•â•â•â•â•â•â•â–¼â•â•â•â•â•â•â•â•â•â•—
                  â”Œâ”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â•‘     EVENT        â•‘â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”
                  â”‚           â•‘  Fed Decision    â•‘           â”‚
                  â”‚           â•‘   March 2025     â•‘           â”‚
                  â”‚           â•šâ•â•â•â•â•â•â•â•â•¤â•â•â•â•â•â•â•â•â•â•           â”‚
            involvesâ”‚                 â”‚ affects_directly    â”‚involves
                  â”‚            governed_by_rule              â”‚
                  â–¼                   â”‚                      â–¼
          â”Œâ”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”      â”Œâ”€â”€â”€â”€â”€â”€â”€â”€â–¼â”€â”€â”€â”€â”€â”€â”€â”€â”   â”Œâ”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”
          â”‚  ENTITY   â”‚      â”‚     RULE        â”‚   â”‚   ENTITY      â”‚
          â”‚  Federal  â”‚      â”‚ "Rate hike iff  â”‚   â”‚  CPI / PCE    â”‚
          â”‚  Reserve  â”‚      â”‚  PCE > 2.5%"   â”‚   â”‚  Inflation    â”‚
          â””â”€â”€â”€â”€â”€â”¬â”€â”€â”€â”€â”€â”˜      â””â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”˜   â””â”€â”€â”€â”€â”€â”€â”€â”¬â”€â”€â”€â”€â”€â”€â”€â”˜
                â”‚                                           â”‚
          related_to                                  related_to
                â”‚                                           â”‚
    â”Œâ”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â–¼â”€â”€â”€â”€â”€â”€â”                     â”Œâ”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â–¼â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”
    â”‚  RELATED MARKET  â”‚                     â”‚    RELATED MARKET      â”‚
    â”‚  Crude Oil       â”‚                     â”‚    USD Index Q1        â”‚
    â”‚  hits $95 EOM    â”‚                     â”‚    above 104.5         â”‚
    â””â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”¬â”€â”€â”€â”€â”€â”€â”˜                     â””â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”¬â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”˜
                â”‚                                           â”‚
          affects_indirectly                          affects_indirectly
                â”‚                                           â”‚
    â”Œâ”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â–¼â”€â”€â”€â”€â”€â”€â”€â”€â”                   â”Œâ”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â–¼â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”
    â”‚   HYPOTHESIS       â”‚                   â”‚      HYPOTHESIS        â”‚
    â”‚  "Energy sector    â”‚                   â”‚  "EM currency stress   â”‚
    â”‚   repricing wave"  â”‚                   â”‚   accelerates Q2"      â”‚
    â””â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”˜                   â””â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”˜
```

Every node is **interactive**: hover to preview, click to lock the graph and read the full profile in the inspector panel. Every edge carries a typed relationship, a confidence score, and an explanation.

---

## Features

<table>
<tr>
<td width="50%">

### ðŸ¦‹ Butterfly Graph Visualization
An interactive Cytoscape.js graph with six node types and seven typed edge relationships. Color-coded, confidence-weighted, and instantly filterable by type and threshold.

</td>
<td width="50%">

### âš¡ Live Polymarket Resolution
Every graph is backed by real Polymarket Gamma API data â€” live probabilities, market outcomes, volume, liquidity, and canonical event URLs attached to every node.

</td>
</tr>
<tr>
<td>

### ðŸ” Related Market Discovery
Shared tags, overlapping entity terms, and narrative proximity surface adjacent contracts you'd never have searched for. Each related market becomes a first-class graph node.

</td>
<td>

### ðŸ¤– Claude AI Graph Expansion
An optional Anthropic pass expands the seed graph with up to 4 new causal nodes and 6 new edges, refines edge explanations, reviews structural quality, and returns a `quality_score`.

</td>
</tr>
<tr>
<td>

### ðŸ”’ Hover-to-Preview Â· Click-to-Lock
Hover any node or edge for an instant preview in the right inspector panel. Click once to lock the graph â€” the layout freezes while you read. Press `Esc` to release.

</td>
<td>

### ðŸ“š Persistent Run History
Every analysis is saved as a `GraphRun` database record with full payload, workflow log, and graph stats. Reload any past graph, trigger a re-review, and browse your history in the drawer.

</td>
</tr>
<tr>
<td>

### ðŸ“¤ Export & Share
Download the full graph payload as a structured JSON file. Copy a shareable permalink to any run with one click. No account required.

</td>
<td>

### Keyboard-First
`F` fit | `R` relayout | `L` labels | `P` strongest path | `H` history | `?` shortcuts | `Esc` clear. Everything is reachable without lifting your hands from the keyboard.

</td>
</tr>
</table>

---

## Quick Start

**Prerequisites:** Python 3.12, pip

```powershell
# 1. Clone and enter the repo
git clone https://github.com/Zwc-11/Chaoswing.git
cd chaoswing

# 2. Create a virtual environment
py -3.12 -m venv .venv
.venv\Scripts\Activate.ps1       # Windows PowerShell
# source .venv/bin/activate       # macOS / Linux

# 3. Install the package + dependencies
pip install -e .

# 4. Apply database migrations
python manage.py migrate

# 5. Start the development server
python manage.py runserver
```

Open **[http://127.0.0.1:8000/](http://127.0.0.1:8000/)** â€” the landing page loads instantly.  
Click **Launch App** â†’ paste any Polymarket event URL â†’ watch the graph appear.

---

## Configuration

ChaosWing auto-loads a root `.env` file on startup. Copy the example and fill in what you need:

```powershell
Copy-Item .env.example .env
```

| Variable | Default | Description |
|---|---|---|
| `DJANGO_SECRET_KEY` | *(insecure dev key)* | **Required in production.** Use a long random secret. |
| `DJANGO_DEBUG` | `1` | Set to `0` for production. Enables security hardening. |
| `DJANGO_ALLOWED_HOSTS` | `127.0.0.1,localhost` | Comma-separated allowed host names. |
| `DJANGO_CSRF_TRUSTED_ORIGINS` | *(empty)* | Required when behind a reverse proxy or custom domain. |
| `CHAOSWING_ENABLE_REMOTE_FETCH` | `1` | Enables live Polymarket Gamma API resolution. |
| `CHAOSWING_ENABLE_LLM` | `0` | Enables Claude graph expansion and review. |
| `ANTHROPIC_API_KEY` | *(empty)* | Your Anthropic API key â€” only needed when LLM is enabled. |
| `ANTHROPIC_MODEL` | `claude-sonnet-4-6` | The Claude model identifier to use. |
| `CHAOSWING_HTTP_TIMEOUT_SECONDS` | `8` | Timeout for outbound Polymarket and image fetch requests. |
| `CHAOSWING_LOG_LEVEL` | `INFO` | Backend log verbosity (`DEBUG`, `INFO`, `WARNING`, `ERROR`). |

**Minimal local setup with AI enabled:**

```env
DJANGO_SECRET_KEY=replace-me-with-a-long-random-secret
DJANGO_DEBUG=1
CHAOSWING_ENABLE_REMOTE_FETCH=1
CHAOSWING_ENABLE_LLM=1
ANTHROPIC_API_KEY=sk-ant-...
```

---

## Architecture

```
chaoswing/
â”œâ”€â”€ apps/web/
â”‚   â”œâ”€â”€ services/
â”‚   â”‚   â”œâ”€â”€ polymarket.py          # Gamma API client + event resolution
â”‚   â”‚   â”œâ”€â”€ contracts.py           # PolymarketEventSnapshot, RelatedEventCandidate
â”‚   â”‚   â”œâ”€â”€ graph_builder.py       # GraphConstructionService â€” builds the seed graph
â”‚   â”‚   â”œâ”€â”€ graph_workflow.py      # GraphWorkflowService â€” orchestrates the full pipeline
â”‚   â”‚   â”œâ”€â”€ anthropic_agent.py     # AnthropicGraphAgent â€” LLM expand + review
â”‚   â”‚   â””â”€â”€ icons.py               # Remote image fetch â†’ data URI
â”‚   â”‚
â”‚   â”œâ”€â”€ templates/web/
â”‚   â”‚   â”œâ”€â”€ landing.html           # Marketing landing page (/)
â”‚   â”‚   â”œâ”€â”€ dashboard.html         # Three-panel app shell (/app/)
â”‚   â”‚   â””â”€â”€ partials/              # Django-rendered inspector partials
â”‚   â”‚
â”‚   â”œâ”€â”€ static/web/
â”‚   â”‚   â”œâ”€â”€ css/
â”‚   â”‚   â”‚   â”œâ”€â”€ tokens.css         # Design system tokens (colors, spacing, type, motion)
â”‚   â”‚   â”‚   â”œâ”€â”€ base.css           # Global resets and base styles
â”‚   â”‚   â”‚   â”œâ”€â”€ layout.css         # Three-panel shell layout + run history drawer
â”‚   â”‚   â”‚   â”œâ”€â”€ components.css     # All UI components (buttons, toasts, cards, etc.)
â”‚   â”‚   â”‚   â”œâ”€â”€ dashboard.css      # Graph stage, HUD, inspector, loading states
â”‚   â”‚   â”‚   â”œâ”€â”€ landing.css        # Landing page styles
â”‚   â”‚   â”‚   â””â”€â”€ motion.css         # Animation system (keyframes, transitions)
â”‚   â”‚   â””â”€â”€ js/
â”‚   â”‚       â”œâ”€â”€ main.js            # App entry point â€” wires all modules together
â”‚   â”‚       â”œâ”€â”€ state.js           # Immutable state container with subscriber pattern
â”‚   â”‚       â”œâ”€â”€ graph.js           # Cytoscape controller â€” render, layout, hover, select
â”‚   â”‚       â”œâ”€â”€ graph-effects.js   # Visual effect layer â€” highlight, dim, path pulse
â”‚   â”‚       â”œâ”€â”€ graph-toolbar.js   # Toolbar binding â€” fit, relayout, labels, path
â”‚   â”‚       â”œâ”€â”€ inspector.js       # Inspector renderer â€” node and edge profiles
â”‚   â”‚       â”œâ”€â”€ controls.js        # Left-rail control binding
â”‚   â”‚       â”œâ”€â”€ api.js             # Fetch helpers â€” graph, runs, export, share URL
â”‚   â”‚       â”œâ”€â”€ toast.js           # Toast notification system
â”‚   â”‚       â”œâ”€â”€ animations.js      # Stage pulse and other DOM animations
â”‚   â”‚       â””â”€â”€ utils.js           # Shared utilities â€” formatDate, deepClone, etc.
â”‚   â”‚
â”‚   â”œâ”€â”€ views.py                   # landing(), dashboard() views
â”‚   â”œâ”€â”€ api_views.py               # graph_from_url(), list_graph_runs(), graph_run_detail()
â”‚   â”œâ”€â”€ partial_views.py           # inspector_node(), inspector_edge(), inspector_empty()
â”‚   â”œâ”€â”€ models.py                  # GraphRun â€” UUID pk, full payload, workflow log
â”‚   â””â”€â”€ urls.py                    # Route table for the web app
â”‚
â”œâ”€â”€ chaoswing/
â”‚   â”œâ”€â”€ config.py                  # Centralized env parsing via RuntimeConfig dataclass
â”‚   â”œâ”€â”€ settings.py                # Django settings â€” driven entirely by RuntimeConfig
â”‚   â””â”€â”€ urls.py                    # Root URL conf â€” delegates to apps.web.urls
â”‚
â”œâ”€â”€ tests/                         # Test suite
â”œâ”€â”€ docs/                          # Documentation and assets
â”œâ”€â”€ manage.py
â””â”€â”€ pyproject.toml
```

### Data Flow

```
User pastes URL
      â”‚
      â–¼
POST /api/v1/graph/from-url/
      â”‚
      â–¼
GraphWorkflowService.run()
      â”‚
      â”œâ”€ PolymarketMetadataService.hydrate()     â†’ PolymarketEventSnapshot
      â”‚    â””â”€ GammaPolymarketClient              â†’ live API or HTML fallback
      â”‚
      â”œâ”€ RelatedMarketDiscoveryService.discover() â†’ [RelatedEventCandidate]
      â”‚    â””â”€ tag + term overlap scoring
      â”‚
      â”œâ”€ GraphConstructionService.build()         â†’ seed graph payload
      â”‚    â””â”€ 6 node types Ã— 7 edge types
      â”‚
      â”œâ”€ AnthropicGraphAgent.expand_graph()       â†’ node/edge additions (optional)
      â”‚    â””â”€ claude-sonnet-4-6, structured JSON
      â”‚
      â”œâ”€ AnthropicGraphAgent.review_graph()       â†’ quality_score + issues (optional)
      â”‚
      â”œâ”€ _validate_payload()                      â†’ schema check
      â”‚
      â””â”€ GraphRun.objects.create()               â†’ persisted to SQLite
            â”‚
            â–¼
      JSON response â†’ Cytoscape.js renders graph
```

### Node & Edge Schema

**Node types** (6): `Event` Â· `Entity` Â· `RelatedMarket` Â· `Evidence` Â· `Rule` Â· `Hypothesis`

**Edge types** (7): `mentions` Â· `involves` Â· `supported_by` Â· `related_to` Â· `affects_directly` Â· `affects_indirectly` Â· `governed_by_rule`

Every node carries: `id`, `label`, `type`, `confidence`, `summary`, `source_url`, `metadata[]`, `evidence_snippets[]`, `probability`

Every edge carries: `id`, `source`, `target`, `type`, `confidence`, `explanation`

---

## Management Commands

```powershell
# Run all checks, tests, and a deterministic smoke run
python manage.py verify_chaoswing

# Generate a full graph run from a URL (no browser needed)
python manage.py run_graph_agent "https://polymarket.com/event/fed-decision-in-march-885"

# Trigger an AI review of a saved run by UUID
python manage.py review_graph_run <run-uuid>

# Django system checks
python manage.py check

# Run the test suite
python manage.py test
```

---

## API Reference

| Method | Endpoint | Description |
|---|---|---|
| `POST` | `/api/v1/graph/from-url/` | Resolve a Polymarket URL and return a full graph payload |
| `GET` | `/api/v1/runs/` | List saved `GraphRun` records (paginated, `?limit=&offset=`) |
| `GET` | `/api/v1/runs/<uuid>/` | Fetch the full payload for a single saved run |
| `POST` | `/api/v1/runs/<uuid>/review/` | Trigger a fresh AI review of a saved run |
| `GET` | `/partials/inspector/empty/` | Django-rendered empty inspector partial |
| `POST` | `/partials/inspector/node/` | Django-rendered node inspector partial |
| `POST` | `/partials/inspector/edge/` | Django-rendered edge inspector partial |

**`POST /api/v1/graph/from-url/`**

```json
// Request
{ "url": "https://polymarket.com/event/fed-decision-in-march-885" }

// Response (abbreviated)
{
  "event": {
    "title": "Fed Decision in March",
    "status": "active",
    "source_url": "https://polymarket.com/event/fed-decision-in-march-885",
    "tags": ["macro", "fed", "rates"],
    "outcomes": ["Yes", "No"],
    "updated_at": "2025-03-15T12:00:00Z"
  },
  "graph": {
    "nodes": [ { "id": "evt-001", "type": "Event", "label": "Fed Decision in March", ... } ],
    "edges": [ { "id": "edge-001", "source": "evt-001", "target": "ent-001", "type": "involves", ... } ]
  },
  "run": {
    "id": "550e8400-e29b-41d4-a716-446655440000",
    "mode": "resolved-backend",
    "generated_at": "2025-03-15T12:00:01Z",
    "workflow": [ ... ]
  }
}
```

---

## Development

```powershell
# Install dev dependencies (ruff, pytest extras, etc.)
pip install -e .[dev]

# Lint with ruff
ruff check .

# Auto-fix linting issues
ruff check --fix .

# Run tests
python manage.py test

# Verify everything in one shot
python manage.py verify_chaoswing
```

All runtime configuration goes through `chaoswing/config.py`. **Never** call `os.getenv()` directly in application code â€” add a typed field to `RuntimeConfig` and read it from `settings`.

---

## Roadmap

- [ ] **Richer evidence retrieval** â€” news API and web search integration beyond market-native metadata
- [ ] **Run comparison view** â€” load two GraphRuns side-by-side and diff the causal graphs
- [ ] **Kalshi adapter** â€” plug in Kalshi contracts alongside Polymarket events
- [ ] **Background workers** â€” move long-running LLM passes to Celery or Django-Q
- [ ] **Graph timeline** â€” animate the evolution of a market's causal web over time
- [ ] **Re-rank related markets** â€” better scoring with embedding similarity and replayable traces
- [ ] **Export to PNG/SVG** â€” one-click visual export of the graph canvas
- [ ] **Webhook support** â€” trigger graph updates when market odds cross a threshold
- [ ] **Multi-market session** â€” analyze multiple events in one workspace, with cross-graph edges
- [ ] **Public graph gallery** â€” shareable read-only graph pages, no account required

---

## Contributing

Contributions are welcome and appreciated. See [CONTRIBUTING.md](CONTRIBUTING.md) for the full guide.

**Quick path:**

1. Fork the repository
2. Create a feature branch: `git checkout -b feat/your-feature`
3. Make your changes and add tests
4. Run `python manage.py verify_chaoswing` â€” all checks must pass
5. Open a pull request with a clear description

**Reporting bugs:** Open an issue with a minimal reproduction, the Django version, and the Python version.

**Adding new node/edge types:** All allowed types are defined in `graph_workflow.py` (`ALLOWED_NODE_TYPES`, `ALLOWED_EDGE_TYPES`). Update the type maps, the graph builder, the inspector renderer, the CSS token map, and the legend â€” then add a test.

**Adding new configuration keys:** Add a typed field to `RuntimeConfig` in `chaoswing/config.py`. Access it via `settings.CHAOSWING_*`. Never use `os.getenv()` elsewhere.

---

## Design Principles

| Principle | What it means in practice |
|---|---|
| **Graph-first** | The graph is not a visualization add-on. It is the product. Every backend decision exists to make the graph richer and more trustworthy. |
| **Source-grounded** | Every node links back to a source market URL. The graph never invents facts â€” it surfaces and connects real market signals. |
| **Replay-safe** | Saved `GraphRun` records are immutable snapshots. You can always reload, re-review, and compare without re-fetching live data. |
| **Config-centralized** | All environment parsing lives in `chaoswing/config.py`. No secret or flag is scattered across the codebase. |
| **LLM-optional** | The full graph generation pipeline works without any API keys. AI expansion and review are additive, not required. |
| **Deterministic fallback** | When the Polymarket API is unavailable, ChaosWing falls back to a deterministic graph so development and testing never depend on external services. |

---

## License

MIT License â€” see [LICENSE](LICENSE) for the full text.

---

<div align="center">

Built with Django Â· Cytoscape.js Â· Claude Â· Polymarket Gamma API

<br/>

**ChaosWing** â€” *One URL. One graph. The whole butterfly effect.*

</div>
