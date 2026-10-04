DOCKER ?= docker
GO ?= go
PYTHON ?= python3
ENV_FILE := $(if $(wildcard .env),.env,.env.example)
COMPOSE = $(DOCKER) compose --env-file $(ENV_FILE) -f infra/docker-compose.yml

.PHONY: up backend down logs test test-infra smoke migrate config check-app

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
	@if test -d services/processor/tests; then $(PYTHON) -m pytest services/processor/tests; else echo 'Processor tests are not present yet (Developer 2).'; fi

test-infra:
	$(COMPOSE) --profile tests run --rm api-tests

smoke: check-app
	@test -f scripts/smoke.py || { echo 'Developer 2 smoke script scripts/smoke.py is not present.' >&2; exit 1; }
	$(PYTHON) scripts/smoke.py

migrate:
	$(COMPOSE) run --rm migrate

config:
	$(COMPOSE) --profile app --profile tests config --quiet

check-app:
	@test -f services/processor/Dockerfile || { echo 'Developer 2 processor Dockerfile is not present; use make backend.' >&2; exit 1; }
	@test -f apps/web/Dockerfile || { echo 'Developer 2 web Dockerfile is not present; use make backend.' >&2; exit 1; }
