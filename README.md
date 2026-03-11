# ChaosWing

ChaosWing is a graph-first market intelligence application for prediction markets.

Paste a single Polymarket event URL and ChaosWing turns it into an interactive butterfly graph showing related entities, evidence, rules, adjacent markets, and second-order impact paths.

![Python](https://img.shields.io/badge/Python-3.12-3776AB?style=flat-square&logo=python&logoColor=white)
![Django](https://img.shields.io/badge/Django-5.x-092E20?style=flat-square&logo=django&logoColor=white)
![Cytoscape.js](https://img.shields.io/badge/Cytoscape.js-3.30-F7941D?style=flat-square)
![License](https://img.shields.io/badge/license-MIT-blue?style=flat-square)

## Repository Description

ChaosWing turns a Polymarket event into an interactive butterfly graph showing related entities, evidence, rules, adjacent markets, and second-order impact paths.

## What It Does

Given one Polymarket event URL, ChaosWing:

1. Resolves the source market from Polymarket's public Gamma API.
2. Pulls live market metadata such as title, outcomes, probabilities, volume, liquidity, and image data.
3. Discovers related markets using shared tags, shared terms, and narrative overlap.
4. Builds a typed graph with `Event`, `Entity`, `RelatedMarket`, `Evidence`, `Rule`, and `Hypothesis` nodes.
5. Renders the result in a Django-served, Cytoscape.js-powered investigation workspace.
6. Optionally runs an Anthropic review and graph-expansion pass when LLM support is enabled.
7. Persists each graph run so it can be reopened, reviewed, exported, or shared.

## Product Surface

- `/` renders the public landing page.
- `/app/` renders the main three-panel graph workspace.
- `POST /api/v1/graph/from-url/` resolves a Polymarket URL and returns the graph payload.
- `GET /api/v1/runs/` lists saved graph runs.
- `GET /api/v1/runs/<uuid>/` loads a saved run.
- `POST /api/v1/runs/<uuid>/review/` triggers a review pass for a saved run.

## Core Features

- Interactive Cytoscape.js butterfly graph with pan, zoom, relayout, hover preview, and click-to-lock inspection.
- Live Polymarket resolution backed by the Gamma API, with graceful fallback behavior when remote resolution is unavailable.
- Confidence-aware edges, related-market discovery, strongest-path focus, export, share URL support, and run history.
- Django templates for the application shell, with modular Vanilla JavaScript for graph behavior, inspector rendering, controls, and toolbar actions.
- Environment-driven configuration suitable for a public GitHub repository.

## Tech Stack

- Python 3.12+
- Django 5+
- Vanilla JavaScript with ES modules
- Cytoscape.js
- SQLite for local persistence
- Anthropic Claude Sonnet 4.6 for optional graph review and expansion

## Quick Start

### 1. Clone the repository

```powershell
git clone https://github.com/Zwc-11/Chaoswing.git
cd Chaoswing
```

### 2. Create and activate a virtual environment

```powershell
py -3.12 -m venv .venv
.venv\Scripts\Activate.ps1
```

### 3. Install dependencies

```powershell
python -m pip install --upgrade pip
python -m pip install -e .
```

### 4. Configure environment variables

Copy the example file:

```powershell
Copy-Item .env.example .env
```

Then edit `.env` as needed.

Minimal local setup:

```env
DJANGO_SECRET_KEY=replace-with-a-long-random-secret
DJANGO_DEBUG=1
CHAOSWING_ENABLE_REMOTE_FETCH=1
CHAOSWING_ENABLE_LLM=0
```

To enable Anthropic-backed review and expansion:

```env
CHAOSWING_ENABLE_LLM=1
ANTHROPIC_API_KEY=your-key-here
ANTHROPIC_MODEL=claude-sonnet-4-6
```

### 5. Apply migrations and run the server

```powershell
python manage.py migrate
python manage.py runserver
```

Open `http://127.0.0.1:8000/`.

## Configuration

ChaosWing reads runtime settings from environment variables through [`chaoswing/config.py`](chaoswing/config.py). Important variables:

| Variable | Default | Purpose |
| --- | --- | --- |
| `DJANGO_SECRET_KEY` | `django-insecure-chaoswing-local` | Django secret key. Set explicitly outside local development. |
| `DJANGO_DEBUG` | `1` | Enables development mode when true. |
| `DJANGO_ALLOWED_HOSTS` | `127.0.0.1,localhost` | Comma-separated host allowlist. |
| `DJANGO_CSRF_TRUSTED_ORIGINS` | empty | Trusted origins for CSRF validation. |
| `CHAOSWING_ENABLE_REMOTE_FETCH` | `1` | Enables live Polymarket resolution. |
| `CHAOSWING_ENABLE_LLM` | `0` | Enables Anthropic graph expansion and review. |
| `ANTHROPIC_API_KEY` | empty | Anthropic API key. |
| `ANTHROPIC_MODEL` | `claude-sonnet-4-6` | Anthropic model identifier. |
| `CHAOSWING_HTTP_TIMEOUT_SECONDS` | `8` | Timeout for outbound HTTP requests. |
| `CHAOSWING_LOG_LEVEL` | `INFO` | Application log level. |

## Architecture

ChaosWing is structured as a modular monolith.

```text
chaoswing/
|-- apps/web/
|   |-- services/
|   |   |-- polymarket.py
|   |   |-- graph_builder.py
|   |   |-- graph_workflow.py
|   |   |-- anthropic_agent.py
|   |   `-- icons.py
|   |-- static/web/
|   |   |-- css/
|   |   `-- js/
|   |-- templates/web/
|   |-- api_views.py
|   |-- partial_views.py
|   |-- views.py
|   `-- urls.py
|-- chaoswing/
|   |-- config.py
|   |-- settings.py
|   `-- urls.py
|-- docs/
|-- tests/
`-- manage.py
```

### Backend responsibilities

- Resolve live Polymarket event data.
- Build a typed graph payload from market context and related events.
- Optionally enrich and review the graph with Anthropic.
- Persist `GraphRun` records for replay and inspection.

### Frontend responsibilities

- Render the three-panel application shell.
- Load graph payloads from Django endpoints.
- Drive Cytoscape.js rendering and interactions.
- Manage hover preview, click-to-lock selection, filtering, history, export, and sharing.

## Development Commands

```powershell
python manage.py check
python manage.py test
python manage.py verify_chaoswing
python manage.py run_graph_agent "https://polymarket.com/event/fed-decision-in-march-885"
python manage.py review_graph_run <run-uuid>
```

## Testing

The project is covered by Django tests and should pass the following before release:

```powershell
python manage.py check
python manage.py test
python -m compileall chaoswing apps tests
```

## Documentation

Additional design and architecture notes live in:

- [docs/architecture.md](docs/architecture.md)
- [docs/frontend-architecture.md](docs/frontend-architecture.md)
- [docs/api-contracts.md](docs/api-contracts.md)
- [docs/design-principles.md](docs/design-principles.md)

## Contributing

See [CONTRIBUTING.md](CONTRIBUTING.md).

## License

ChaosWing is released under the MIT License. See [LICENSE](LICENSE).
