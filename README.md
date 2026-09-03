# Humble Scrape

ETL + FastAPI API + Vue frontend for extracting public bundles from
[Humble Bundle Books](https://www.humblebundle.com/books), normalizing them,
persisting them with SQLAlchemy, and browsing them through a web interface.

## Current Status

Repository release: `1.0.1`.

The reference branch for this release is `prod`. The real state of this branch
is not "SQLite only": the application supports two database modes through
`DB_DB_TYPE`.

- `postgresql`: used by Docker Compose and the production path.
- `sqlite`: local development mode, used by default when `DB_DB_TYPE` is not
  configured.

Evidence in the project:

- `spider/config/settings.py` defines `DB_DB_TYPE=sqlite|postgresql` and the
  `DB_PGUSER`, `DB_PGPASSWORD`, `DB_PGDATABASE`, `DB_PGHOST`, and `DB_PGPORT`
  variables.
- `spider/database/session.py` builds either `postgresql://...` or
  `sqlite:///...` URIs according to `DB_DB_TYPE`.
- `api/main.py` uses `postgresql+asyncpg://` for FastAPI async sessions and
  `sqlite+aiosqlite://` for SQLite.
- `requirements.txt` includes PostgreSQL drivers (`psycopg`, `psycopg-binary`,
  `psycopg2-binary`, `asyncpg`) and SQLite support (`aiosqlite`).
- `docker-compose*.yml` starts `postgres:16-alpine` and forces the backend to
  `DB_DB_TYPE=postgresql`.
- `humble_bundle.db` was removed from tracking in `prod`; SQLite remains a
  local fallback, not the main production database.

## Stack

- Python 3.12+ / 3.13
- FastAPI + Uvicorn
- SQLAlchemy 2
- PostgreSQL 16 through Docker Compose for production
- SQLite + aiosqlite for optional local development
- Requests + BeautifulSoup4 for scraping
- pandas + Pydantic + pydantic-settings for normalization and configuration
- JWT with `python-jose`
- Password hashing with `bcrypt(SHA-256(password))`
- Vue 3 + Vite + TypeScript + Vue Router
- axios, vue-i18n, Sass, ESLint, Stylelint, Vitest

## Requirements

1. Python 3.12 or 3.13 with `venv` and `pip`.
2. Node.js 20+ for frontend development.
3. Docker and Docker Compose for running the PostgreSQL stack.
4. Network access to run the ETL against Humble Bundle.

## Configuration

Copy the example file and adjust secrets before starting services:

```bash
cp .env.example .env
```

Relevant variables:

```env
DB_DB_TYPE=postgresql
DB_PGUSER=postgres
DB_PGPASSWORD=postgres
DB_PGDATABASE=humble_bundle
DB_PGHOST=postgres
DB_PGPORT=5432
DB_SQL_ECHO=false

DB_JWT_SECRET_KEY=change-this-secret
DB_JWT_ACCESS_TOKEN_EXP_MINUTES=60

DB_ADMIN_USERNAME=admin
DB_ADMIN_EMAIL=admin@example.com
DB_ADMIN_PASSWORD_PLAIN=admin_password

VITE_API_BASE_URL=http://localhost:5002
FRONTEND_PORT=3002
```

Notes:

- In Docker Compose, the `api` service forces `DB_DB_TYPE=postgresql` and uses
  the internal `postgres` host.
- For local execution without Docker, use SQLite with `DB_DB_TYPE=sqlite` and
  `DB_DB_PATH=humble_bundle.db`.
- To use PostgreSQL outside Docker, set `DB_DB_TYPE=postgresql` and point
  `DB_PGHOST` to the real database host.

## Docker Compose

Recommended path for `prod`:

```bash
docker compose up -d --build
```

Services:

- `postgres`: PostgreSQL 16 with the `postgres_data` volume.
- `api`: FastAPI on `http://localhost:5002`.
- `frontend`: Vite preview/container on `http://localhost:${FRONTEND_PORT:-3002}`.

Makefile commands:

- `make docker-up`: runs `docker compose up -d`.
- `make docker-down`: stops services and removes volumes with `down -v`.
- `make docker-restart`: recreates containers from a fresh build.

## Local Installation

Backend:

```bash
python3 -m venv .venv
source .venv/bin/activate
pip install -r requirements.txt
make db-init
make api
```

Frontend:

```bash
cd frontend
npm install
npm run dev
```

Frontend variable:

```env
VITE_API_BASE_URL=http://localhost:5002
```

The frontend client uses `VITE_API_BASE_URL` when it is set. Otherwise it falls
back to `/humblebundlespider/api`, which supports deployments under a proxied
subpath.

## Commands

- `make etl`: runs `python -m spider.cli.run_spider`.
- `make api`: starts FastAPI locally on `http://0.0.0.0:5002`.
- `make db-init`: initializes tables with the current `DB_*` configuration.
- `make db-reset`: removes only the local SQLite file `humble_bundle.db`.
- `make frontend-dev`: starts Vite on `http://localhost:3002`.
- `make frontend-build`: builds the frontend for production.
- `make docker-up`: starts the Docker stack with PostgreSQL.
- `make docker-down`: stops the Docker stack and removes volumes.
- `make docker-restart`: recreates the Docker stack with a fresh build.

Important: `make db-reset` does not reset PostgreSQL. To clear the Docker
database, use `make docker-down` or `docker compose down -v`.

## ETL

```bash
source .venv/bin/activate
python -m spider.cli.run_spider
# or
make etl
```

Flow:

1. Downloads `https://www.humblebundle.com/books`.
2. Extracts `script#landingPage-json-data`.
3. Normalizes products with pandas.
4. Converts dates, URLs, JSON lists, and derived metrics.
5. Visits each bundle page and reads `webpack-bundle-page-data`.
6. Extracts tiers, books, total MSRP, and raw HTML.
7. Validates records with Pydantic.
8. Archives expired bundles without deleting their metadata.
9. Persists bundles by `machine_name` and stores raw snapshots with hashes.

## API

The FastAPI metadata for this release is `1.0.1`.

Public endpoints:

- `GET /health`: service status.
- `GET /bundles`: active bundles ordered by closing date.
- `GET /bundles?include_inactive=true`: includes retained inactive bundles, including expired/archived and other non-current records.
- `GET /bundles/{bundle_id}`: bundle by UUID, including retained inactive bundles when the UUID is known.
- `GET /bundles/by-machine-name/{machine_name}`: bundle by `machine_name`, including retained inactive bundles.
- `GET /bundles/featured`: featured bundle by MSRP and sales.
- `GET /landing-page-raw-data`: raw snapshots.
- `GET /landing-page-raw-data/latest`: latest raw snapshot.
- `GET /landing-page-raw-data/{raw_data_id}`: raw snapshot by UUID.

The detail endpoints intentionally remain public for known identifiers, including retained `raw_html`; collection endpoints hide non-current rows unless explicitly requested.

Authentication endpoints:

- `POST /auth/login`: validates username/password and returns a JWT.
- `GET /auth/me`: returns the authenticated user.

Protected endpoint:

- `POST /etl/run`: runs the ETL and requires `Authorization: Bearer <token>`.

## Database migration

At startup, the API and ETL add the nullable `archived_at` column and its index if needed. Startup also normalizes any row with `archived_at` set to `is_active=false`; this is a one-way safety correction. Back up the SQLite file before deployment so rollback can restore the pre-migration database.

The lifecycle states are intentionally small: current (`is_active=true`, `archived_at=NULL`), archived/expired (`is_active=false`, `archived_at` set), and non-current/unarchived (`is_active=false`, `archived_at=NULL`, such as scheduled or source-inactive data). The opt-in collection returns both non-current categories; the UI presents them together as inactive.

## Tests

Install the development dependencies and run the pytest suite:

```bash
pip install -r requirements-dev.txt
python -m pytest
```

## Authentication

The `User` model is persisted in the same database configured by `DB_DB_TYPE`.

Automatic seed:

1. Define `DB_ADMIN_USERNAME` and `DB_ADMIN_EMAIL`.
2. Define one of these variables:
   - `DB_ADMIN_PASSWORD_PLAIN`: plain password; the backend normalizes it.
   - `DB_ADMIN_PASSWORD_SHA`: 64-character SHA-256 hex digest.
3. When the API starts, `ensure_admin_user` creates or updates the user.

Useful commands:

```bash
python -m spider.cli.create_user \
  --username admin \
  --email admin@example.com \
  --password "admin_password"

python -m spider.cli.hash_password --password "admin_password"
```

Hashing:

- The backend normalizes passwords as `SHA-256(plain)` when it receives plain
  text.
- The stored hash is `bcrypt(SHA-256(password))`.
- `api/security.py` accepts plain text or SHA-256 hex input for manual
  operations.

## Frontend

The frontend is a Vue 3 SPA with router:

- `/`: main view with bundles, utilities, and controls.
- `/login`: login against `POST /auth/login`.

Features:

- Light/dark theme.
- `es`/`en` language switcher.
- JWT session stored in `localStorage`.
- Floating logout button.
- ETL execution from the UI when a token is available.
- Bundle and raw data views with JSON open/download utilities.

Scripts:

- `npm run dev`
- `npm run build`
- `npm run preview`
- `npm run lint`
- `npm run lint:style`
- `npm run test:run`
- `npm run test:coverage`

## Architecture

- `api/`: FastAPI, auth, HTTP schemas, and endpoints.
- `spider/core/`: `HumbleSpider` and domain errors.
- `spider/scrapers/`: per-bundle detail scraper.
- `spider/database/`: SQLAlchemy models, sessions, persistence, and user seed.
- `spider/schemas/`: ETL Pydantic models.
- `spider/config/`: `DB_*` settings.
- `spider/cli/`: ETL, user creation, and password hash commands.
- `frontend/src/views/`: main Vue Router views.
- `frontend/src/components/`: cards, utilities, base UI, and floating controls.
- `frontend/src/composables/`: auth, bundles, raw data, responsive, dark mode.
- `docker-compose*.yml`: PostgreSQL + API + frontend stack.
- `tests/`: backend security tests.

## Database

Current tables:

- `bundle`: normalized metadata, dates, active state, tiers, books, images,
  MSRP, and raw HTML.
- `landing_page_raw_data`: snapshots of `landingPage-json-data`, date, source
  URL, hash, and optional version.
- `user`: authentication users with `username`, `email`, `password_hash`, and
  `created_at`.

PostgreSQL mode:

- Enabled with `DB_DB_TYPE=postgresql`.
- Requires `DB_PGUSER`, `DB_PGPASSWORD`, and `DB_PGDATABASE`.
- Uses `psycopg` for sync operations and `asyncpg` for FastAPI async sessions.
- In Docker, `DB_PGHOST=postgres`.

SQLite mode:

- Enabled with `DB_DB_TYPE=sqlite`.
- Uses `DB_DB_PATH=humble_bundle.db`.
- Intended for local development and quick tests.

## Verification

Backend:

```bash
python -m compileall -q api spider tests
python -m unittest tests/test_security.py
```

Frontend:

```bash
cd frontend
npm run test:run
npm run build
```

Docker:

```bash
docker compose up -d --build
docker compose ps
```
