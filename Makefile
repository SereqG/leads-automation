.PHONY: up down build logs migrate migration cli shell-backend dbshell test-backend lint celery-worker celery-beat

up:
	podman-compose up -d

down:
	podman-compose down

build:
	podman-compose build

logs:
	podman-compose logs -f $(s)

migrate:
	podman-compose exec api alembic upgrade head

migration:
	podman-compose exec api alembic revision --autogenerate -m "$(m)"

cli:
	podman-compose exec api python cli.py $(cmd)

shell-backend:
	podman-compose exec api /bin/bash

dbshell:
	podman-compose exec postgres sh -c 'psql -U "$$POSTGRES_USER" -d "$$POSTGRES_DB"'

test-backend:
	podman-compose exec api pytest

lint:
	podman-compose exec api sh -c "ruff check . && black --check ."

celery-worker:
	podman-compose up -d worker

celery-beat:
	podman-compose up -d beat
