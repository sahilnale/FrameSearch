DOCKER ?= docker
GO ?= go
PYTHON ?= python3
UV ?= uv
API_URL ?= http://localhost:8080
SMOKE_ARGS ?=
ENV_FILE ?= .env
COMPOSE = $(DOCKER) compose --env-file $(ENV_FILE) -f infra/docker-compose.yml

.PHONY: setup-env up backend down logs test test-infra smoke smoke-recovery reconcile reconcile-stale migrate config check-app

setup-env:
	$(PYTHON) infra/setup_env.py

up: check-app
	$(COMPOSE) --profile app up --build

backend:
	$(COMPOSE) up -d --build postgres migrate kafka kafka-init minio minio-init api

down:
	$(COMPOSE) --profile app --profile tests down

logs:
	$(COMPOSE) --profile app logs -f

test:
	cd services/api && $(GO) test ./...
	cd services/processor && $(UV) run --frozen pytest

test-infra:
	$(COMPOSE) --profile tests run --rm api-tests

smoke: check-app
	FRAMESEARCH_COMPOSE_COMMAND='$(COMPOSE) --profile app' $(PYTHON) infra/smoke.py --api-url '$(API_URL)' $(SMOKE_ARGS)

smoke-recovery: check-app
	FRAMESEARCH_COMPOSE_COMMAND='$(COMPOSE) --profile app' $(PYTHON) infra/recovery_smoke.py --api-url '$(API_URL)' --allow-worker-stop $(SMOKE_ARGS)

reconcile:
	$(COMPOSE) run --rm --no-deps api --reconcile

reconcile-stale: check-app
	$(COMPOSE) --profile app stop processor
	$(COMPOSE) run --rm --no-deps api --reconcile --include-stale --processor-stopped
	$(COMPOSE) --profile app start processor

migrate:
	$(COMPOSE) run --rm migrate

config:
	$(COMPOSE) --profile app --profile tests config --quiet

check-app:
	@test -f services/processor/Dockerfile || { echo 'Developer 2 processor Dockerfile is not present; use make backend.' >&2; exit 1; }
	@test -f apps/web/Dockerfile || { echo 'Developer 2 web Dockerfile is not present; use make backend.' >&2; exit 1; }
