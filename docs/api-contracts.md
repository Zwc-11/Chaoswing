# API Contracts

The canonical ChaosWing API reference is now generated from the endpoint catalog in
[`apps/web/services/api_reference.py`](../apps/web/services/api_reference.py).

Use these as the source of truth:

- Human-readable docs page: `/developers/api/`
- Machine-readable schema: `/api/openapi.json`
- Discovery document: `/api/`

Why this changed:

- the old markdown contract drifted as the API grew
- examples, rate limits, and response behavior now come from the live runtime
- the OpenAPI schema and browser docs share the same endpoint metadata, so updates stay synchronized

For local development, start the server and open:

```text
http://127.0.0.1:8000/developers/api/
http://127.0.0.1:8000/api/openapi.json
http://127.0.0.1:8000/api/
```
