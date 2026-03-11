# ChaosWing

ChaosWing is a graph-first causal intelligence system for prediction markets. A user pastes a Polymarket event URL and ChaosWing turns that event into an interactive butterfly graph showing related entities, evidence, rules, adjacent market contracts, and direct or second-order impact paths.

The current build is a real Django + JavaScript application, not a static mock. Django owns routing, CSRF-safe form handling, graph-generation endpoints, persisted run storage, and server-rendered inspector partials. Vanilla JavaScript owns the Cytoscape graph stage, live controls, and graph interaction model.

For public-repository safety, runtime configuration is centralized in `chaoswing/config.py`. Secrets and deployment knobs are loaded from environment variables, with a root `.env` file supported for local development only.

## GitHub Description

Short description:

`ChaosWing turns a Polymarket event into an interactive butterfly graph showing related entities, evidence, rules, adjacent markets, and second-order impact paths.`

Longer repository intro:

`ChaosWing is a graph-first market intelligence system for prediction markets. Paste one Polymarket event URL and explore a live causal graph built with Django, Cytoscape.js, and a backend workflow that resolves the event, finds related markets, attaches source context, and prepares the graph for optional LLM expansion and review.`

## Feature Overview

- Django-rendered shell at `/app/`
- `POST /api/v1/graph/from-url/` backend workflow with persisted `GraphRun` records
- real Polymarket resolution through Gamma when available, with deterministic fallback when it is not
- related-market discovery from Polymarket event metadata
- graph construction layer for Event, Entity, RelatedMarket, Evidence, Rule, and Hypothesis nodes
- optional Anthropic expansion and review workflow using `claude-sonnet-4-6`
- saved run retrieval and re-review endpoints
- image-backed graph nodes and inspector links back to source markets

## Local Setup

```powershell
py -3.12 -m venv .venv
.venv\Scripts\Activate.ps1
python -m pip install --upgrade pip
python -m pip install -e .
python manage.py migrate
python manage.py runserver
```

Open `http://127.0.0.1:8000/app/`.

For contributor tooling:

```powershell
python -m pip install -e .[dev]
ruff check .
```

## Configuration

ChaosWing auto-loads a root `.env` file on startup through `chaoswing/config.py`. Copy `.env.example` to `.env` and fill in the values you need. The environment is the only supported place for secrets such as `DJANGO_SECRET_KEY` and `ANTHROPIC_API_KEY`.

Key variables:

- `DJANGO_SECRET_KEY` must be set to a real secret when `DJANGO_DEBUG=0`
- `DJANGO_ALLOWED_HOSTS` and `DJANGO_CSRF_TRUSTED_ORIGINS` control host and CSRF policy for deployment
- `DJANGO_SECURE_SSL_REDIRECT`, `DJANGO_SESSION_COOKIE_SECURE`, and `DJANGO_CSRF_COOKIE_SECURE` are production hardening toggles
- `CHAOSWING_ENABLE_REMOTE_FETCH=1` enables Gamma-backed Polymarket resolution and icon hydration
- `CHAOSWING_ENABLE_LLM=0` keeps the agent off until an Anthropic key is configured
- `ANTHROPIC_API_KEY` is intentionally blank by default
- `ANTHROPIC_MODEL=claude-sonnet-4-6` is the default Claude model id and can be overridden
- `CHAOSWING_HTTP_TIMEOUT_SECONDS=8` controls outbound Polymarket and image fetch timeouts
- `CHAOSWING_LOG_LEVEL=INFO` controls backend log verbosity

Example:

```env
DJANGO_SECRET_KEY=replace-with-a-long-random-secret
DJANGO_DEBUG=1
DJANGO_ALLOWED_HOSTS=127.0.0.1,localhost
CHAOSWING_ENABLE_REMOTE_FETCH=1
CHAOSWING_ENABLE_LLM=1
ANTHROPIC_API_KEY=your-key-here
ANTHROPIC_MODEL=claude-sonnet-4-6
```

Production note:

- `DJANGO_DEBUG=0` with the default development secret key will raise an error on startup.
- `.env` is ignored by git and intended for local development only.
- Contributors should add new runtime flags to `chaoswing/config.py` instead of calling `os.getenv(...)` directly throughout the codebase.

## Backend Workflow

`GraphWorkflowService` is the current orchestration layer. For each submitted URL it:

1. resolves the source event from Polymarket Gamma when possible
2. discovers related markets from shared tags and narrative overlap
3. constructs a seed butterfly graph from normalized event data
4. attaches source descriptions and icon assets to nodes
5. optionally lets Anthropic expand the graph and review the result
6. validates the final payload
7. persists the run to `GraphRun`

Saved runs can be fetched or re-reviewed later, which gives the backend a real replay seam rather than a fake placeholder.

## Commands

```powershell
python manage.py check
python manage.py test
python manage.py run_graph_agent "https://polymarket.com/event/..."
python manage.py review_graph_run <run-uuid>
python manage.py verify_chaoswing
```

`verify_chaoswing` runs Django checks, the test suite, and a deterministic workflow smoke run.

## Project Structure

```text
chaoswing/
|-- apps/
|   `-- web/
|       |-- api_views.py
|       |-- models.py
|       |-- partial_views.py
|       |-- services/
|       |   |-- anthropic_agent.py
|       |   |-- contracts.py
|       |   |-- graph_builder.py
|       |   |-- graph_workflow.py
|       |   `-- polymarket.py
|       |-- static/web/
|       `-- templates/web/
|-- chaoswing/
|   |-- config.py
|-- docs/
|-- tests/
|-- .github/
|-- manage.py
`-- pyproject.toml
```

## Screenshots

Screenshots are not checked in yet. Planned captures for the repository:

- empty graph stage with the Django shell ready
- resolved graph with image-backed nodes and saved run metadata
- backend workflow command output for run generation and review

## Roadmap

- add richer evidence retrieval beyond market-native metadata
- expand related-market discovery with better ranking and replayable traces
- support persisted run history and comparison views
- add Kalshi adapters alongside Polymarket
- move long-running graph generation and review into background workers

## License

ChaosWing is released under the MIT License. See [LICENSE](LICENSE).
