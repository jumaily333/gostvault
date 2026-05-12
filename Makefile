# GhostVault Intelligence System — Makefile

.DEFAULT_GOAL := help
PYTHON := python3.12
COMPOSE := docker compose

.PHONY: help build up down logs migrate shell test lint format typecheck clean

help:
	@echo ""
	@echo "GhostVault Intelligence System"
	@echo "================================"
	@echo "make build        Build Docker images"
	@echo "make up           Start all services"
	@echo "make down         Stop all services"
	@echo "make logs         Tail API logs"
	@echo "make migrate      Run Alembic migrations"
	@echo "make shell        Open API container shell"
	@echo "make test         Run unit tests"
	@echo "make lint         Run ruff linter"
	@echo "make format       Auto-format with ruff"
	@echo "make typecheck    Run mypy"
	@echo "make clean        Remove containers and volumes"
	@echo ""

build:
	$(COMPOSE) build --no-cache

up:
	cp -n .env.example .env 2>/dev/null || true
	$(COMPOSE) up -d migrate
	$(COMPOSE) up -d api
	@echo ""
	@echo "✓ GhostVault API running at http://localhost:8000"
	@echo "✓ Health: http://localhost:8000/health"
	@echo "✓ Docs:   http://localhost:8000/docs  (non-production only)"
	@echo ""

down:
	$(COMPOSE) down

logs:
	$(COMPOSE) logs -f api

migrate:
	$(COMPOSE) run --rm migrate

shell:
	$(COMPOSE) exec api /bin/bash

test:
	$(PYTHON) -m pytest tests/unit/ -v --tb=short

test-cov:
	$(PYTHON) -m pytest tests/ -v --tb=short --cov=app --cov-report=term-missing

lint:
	$(PYTHON) -m ruff check app/ tests/

format:
	$(PYTHON) -m ruff format app/ tests/

typecheck:
	$(PYTHON) -m mypy app/

clean:
	$(COMPOSE) down -v --remove-orphans
	find . -type d -name __pycache__ -exec rm -rf {} + 2>/dev/null || true
	find . -name "*.pyc" -delete

# ── Local dev (no Docker) ──────────────────────────────────────────────────────
dev-install:
	$(PYTHON) -m pip install -e ".[dev]"

dev-run:
	$(PYTHON) -m uvicorn app.main:app --reload --host 0.0.0.0 --port 8000 --log-level debug
