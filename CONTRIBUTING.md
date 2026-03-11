# Contributing to ChaosWing

ChaosWing is being built as a clean, extensible interface prototype first. Contributions should improve clarity, correctness, and product cohesion before they add surface area.

## Development Workflow

1. Create a Python 3.12 virtual environment.
2. Install the project with `python -m pip install -e .`.
3. Run `python manage.py check` and `python manage.py test` before opening a pull request.
4. If you change the UI, include updated screenshots or a short screen recording in the PR.

## Contribution Standards

- Keep Django views thin and move mocked or future domain logic into dedicated modules.
- Keep frontend code split by responsibility. Avoid adding large all-in-one scripts.
- Reuse existing design tokens and component classes before introducing new visual patterns.
- Write concise documentation updates when behavior, architecture, or contracts change.
- Prefer incremental changes that preserve the backend seam instead of hard-coding UI logic to mock data.
- Route new environment variables through `chaoswing/config.py` instead of calling `os.getenv(...)` ad hoc.
- Keep secrets out of committed files. Local secrets belong in an untracked `.env`.
- Add comments or docstrings only where they reduce ambiguity in non-trivial code paths.

## Pull Requests

Each pull request should explain:

- what changed
- why the change matters
- how it was tested
- whether the mocked API contract or docs were updated

Small, reviewable PRs are preferred over broad rewrites.

## Reporting Issues

Use the GitHub issue templates for reproducible bugs and scoped feature requests. Include the event URL used, the expected graph behavior, and any visible UI regressions.
