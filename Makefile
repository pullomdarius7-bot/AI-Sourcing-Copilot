.PHONY: init up down logs ps build test lint migrate db-shell reset smoke monitoring monitoring-down env-upgrade \
        deploy rollback releases backup backup-verify restore validate health loadtest ssl

COMPOSE ?= docker compose

init:            ## Generate .env with random secrets
	./scripts/init-env.sh

up: init         ## Build and start the full stack
	$(COMPOSE) up -d --build --wait

down:
	$(COMPOSE) down

logs:
	$(COMPOSE) logs -f --tail=100

ps:
	$(COMPOSE) ps

build:
	$(COMPOSE) build

migrate:         ## Apply database migrations
	$(COMPOSE) run --rm migrate

test:            ## Run the backend test-suite against a throwaway database
	$(COMPOSE) --profile test run --rm --build backend-test

lint:
	$(COMPOSE) --profile test run --rm --no-deps backend-test ruff check app tests alembic

db-shell:
	$(COMPOSE) exec db sh -c 'psql -U $$POSTGRES_USER -d $$POSTGRES_DB'

workers:         ## Scale background workers, e.g. make workers N=3
	$(COMPOSE) up -d --scale worker=$${N:-2} worker

monitoring: init ## Start Prometheus (127.0.0.1:9090) and Grafana (127.0.0.1:3001) next to the stack
	$(COMPOSE) --profile monitoring up -d --wait prometheus grafana

monitoring-down:
	$(COMPOSE) --profile monitoring stop prometheus grafana

env-upgrade:     ## Add secrets introduced by newer versions to an existing .env (never changes existing values)
	./scripts/upgrade-env.sh

smoke:           ## End-to-end check through the frontend proxy
	./scripts/smoke.sh

# --- Production operations (docs/DEPLOYMENT.md) ---
deploy:          ## Build and release the code in this directory (REF=<git ref> to fetch and deploy a ref)
	./scripts/deploy.sh $(REF)

rollback:        ## Back to the previous release (VERSION=<release> and MODE=--with-db are optional)
	./scripts/rollback.sh $(VERSION) $(or $(MODE),--keep-db)

releases:        ## List releases kept on this server
	./scripts/rollback.sh --list

backup:          ## Full backup now (database, documents, .env)
	./scripts/backup.sh

backup-verify:   ## Restore the newest backup into a scratch database to prove it works
	./scripts/verify-backup.sh

restore:         ## Restore a backup: make restore FILE=/opt/copilot/backups/copilot-....tar.gpg
	./scripts/restore.sh $(FILE)

validate:        ## Post-deployment validation
	./scripts/validate.sh

health:          ## One health probe (what cron runs every 5 minutes)
	./scripts/healthcheck.sh

ssl:             ## Obtain the first Let's Encrypt certificate
	./scripts/ssl-init.sh

loadtest:        ## k6 load test: make loadtest PROFILE=smoke|load|stress (needs LOADTEST_EMAIL, LOADTEST_PASSWORD)
	./scripts/loadtest.sh $(or $(PROFILE),load)

reset:           ## DESTROYS all data (volumes)
	$(COMPOSE) down -v
