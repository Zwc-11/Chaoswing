PYTHON ?= python
RUN_ID ?= replace-with-run-id

.PHONY: install dev-install migrate run check test lint workflow review verify

install:
	$(PYTHON) -m pip install --upgrade pip
	$(PYTHON) -m pip install -e .

dev-install:
	$(PYTHON) -m pip install --upgrade pip
	$(PYTHON) -m pip install -e .[dev]

migrate:
	$(PYTHON) manage.py migrate

run:
	$(PYTHON) manage.py runserver

check:
	$(PYTHON) manage.py check

test:
	$(PYTHON) manage.py test

lint:
	ruff check .

workflow:
	$(PYTHON) manage.py run_graph_agent "https://polymarket.com/event/will-brent-crude-trade-above-95-before-july-2026"

review:
	$(PYTHON) manage.py review_graph_run $(RUN_ID)

verify:
	$(PYTHON) manage.py verify_chaoswing
