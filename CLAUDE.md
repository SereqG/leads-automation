# Project: LeadGen Platform

Lead-generation platform covering the full pipeline: prospect discovery, data
enrichment/analysis, data storage, and outbound email campaigns.

The platform is exposed two ways over a shared core: a **FastAPI HTTP API** and a
**CLI tool**. Both are thin adapters — no business logic lives in the API routes
or CLI commands themselves; they parse input, call into the service/selector
layer, and format output.

**Stack:** FastAPI (HTTP API) + Typer (CLI) + SQLAlchemy 2.0 / Alembic
(ORM & migrations) + Pydantic (schemas/validation) + PostgreSQL + Celery/Redis
(async & scheduled jobs).

## ⚠️ Non-negotiable rules

- **Ask when in doubt.** If a requirement, data model, naming, or architectural
  choice is ambiguous, STOP and ask a clarifying question instead of guessing.
  Never silently assume — this applies to business logic, schema changes, and
  anything touching external services (email sending, scraping, third-party
  APIs) most of all.
- **Repo boundary.** Never read, list, or reference files outside this
  repository's root directory. No `../` traversal, no absolute paths outside
  the repo, no inspecting the home directory, other projects, or system files.
  If something you need seems to live outside the repo, ask instead of
  looking for it.
- **No destructive commands** (`rm -rf`, force-push, dropping/truncating
  tables, deleting migrations) without explicit confirmation first.
- Never commit `.env`, credentials, API keys, or SMTP/email-provider secrets.

## Architecture

### Backend — FastAPI, Vertical Slice Architecture

Organize by **feature/use-case**, not by technical layer. Each slice owns its
models, schemas, routes, services, selectors, tasks, CLI commands, and tests.
Avoid splitting a feature across per-layer modules when it spans concerns —
colocate what changes together.

```
backend/
  config/                 # settings, FastAPI app factory, Celery app, DB session/engine
  apps/
    prospects/            # slice: prospect discovery & search
      models.py           # SQLAlchemy models
      schemas.py          # Pydantic request/response models (replaces DRF serializers)
      services.py         # business logic, orchestration
      selectors.py        # read/query logic (kept separate from services)
      router.py           # FastAPI routes (thin; call services/selectors)
      cli.py              # Typer commands for this slice (thin; call services/selectors)
      tasks.py            # Celery tasks for this slice
      tests/
    enrichment/           # slice: data gathering & analysis of leads
      ...
    campaigns/            # slice: email sequences & sending
      ...
    accounts/             # slice: auth / users / orgs
      ...
  core/                   # only truly cross-cutting code: base models, shared
                          # dependencies, common utils, exceptions
  migrations/             # Alembic migration environment + versions
  main.py                 # FastAPI entrypoint — mounts each slice's router
  cli.py                  # root Typer app — registers each slice's cli.py
```

Rules:
- A slice should be deletable without breaking unrelated slices.
- `router.py` and `cli.py` are both thin: parse input, call `services.py` /
  `selectors.py`, format output. No business logic in either.
- `core/` is for genuinely shared infrastructure only — resist dumping
  business logic there "to be safe."
- Long-running or scheduled work (scraping, enrichment, bulk email sends)
  goes through Celery tasks defined in the owning slice's `tasks.py`, using
  Redis as the broker.
- Business logic lives in `services.py`, queries in `selectors.py`, validation
  and serialization via Pydantic in `schemas.py`.

### CLI

The CLI is a first-class interface, on par with the HTTP API, and shares the
same service/selector layer.

- Runs **in-process** and calls services/selectors directly — it does **not**
  go over HTTP to the API. (Confirm if you'd rather the CLI be a thin HTTP
  client of the running API instead.)
- Root Typer app in `backend/cli.py` aggregates each slice's `cli.py`.
- Commands stay thin, same rule as routes — no business logic in the command
  functions.
- High-risk actions (real email sends, running against prod data) confirm
  first, same as everywhere else.

## Docker

Everything runs in Docker Compose — FastAPI (api), PostgreSQL, Redis, and
Celery (worker + beat) each as their own service. The CLI runs inside the `api`
container (as a one-off `exec`/`run`), sharing its image and env.

- **Always use the `make` targets below, not raw `docker` / `docker compose`
  commands.** This keeps flags, env files, and service names consistent.
- Never run the API, CLI, `pytest`, or Alembic directly on the host — run them
  inside the relevant container (via the `make` targets, which already do
  `docker compose exec ...`), so behavior matches CI/prod parity.
- Don't edit `docker-compose.yml` / `Dockerfile*` casually — these affect
  every service. Confirm before changing base images, exposed ports, volume
  mounts, or env var wiring.
- Never commit `.env`, `.env.local`, or any file with real secrets. Only
  `.env.example` (with placeholder values) is tracked.
- If a container won't start or a dependency changed (new pip package),
  rebuild the image (`make build`) rather than patching the running container.
- Migrations are generated/run inside the `api` container, never against a DB
  reached from the host.

## Commands (Makefile)

_(fill in exact targets once finalized — placeholders below)_

```bash
make up                     # docker compose up (all services, detached)
make down                   # stop and remove containers
make build                  # rebuild images (after dependency changes)
make logs                   # tail logs for all services
make logs s=api             # tail logs for one service

make migrate                # alembic upgrade head (apply migrations) in api container
make migration m="..."      # alembic revision --autogenerate -m "..."
make cli cmd="..."          # run the Typer CLI inside the api container
make shell-backend          # shell into api container
make dbshell                # psql shell into postgres container

make test-backend           # pytest inside api container
make lint                   # lint backend (ruff/black)

make celery-worker          # start/attach celery worker (if not in compose)
make celery-beat            # start/attach celery beat
```

## Conventions

- Python: type hints required on service/selector functions (and on route/CLI
  signatures); formatted with `black`/`ruff` (confirm exact tooling once
  configured).
- Pydantic models for all request/response bodies and CLI structured output —
  no hand-rolled dict validation.
- API responses: consistent `{ success, data, error }` shape. The CLI mirrors
  this in its machine-readable (`--json`) mode; human-readable output for
  interactive use is fine. (Adjust if a different contract is chosen — ask
  before changing this once set.)
- Alembic migrations are reviewed, never edited after being merged/deployed.
- All outbound email sending goes through the `campaigns` slice's Celery
  tasks — no ad hoc email sends from other slices, routes, or CLI commands.

## Notes / gotchas

- Anything that touches real prospect data or sends real emails should be
  treated as high-risk: confirm before running against production data or
  live email providers.
- (Add project-specific gotchas here as they come up — keep this file lean
  and prune anything Claude already gets right without being told.)