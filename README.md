# Humble Scrape

ETL + API FastAPI + frontend Vue para extraer bundles publicos de
[Humble Bundle Books](https://www.humblebundle.com/books), normalizarlos,
persistirlos con SQLAlchemy y consultarlos desde una interfaz web.

## Estado Actual

Release del repositorio: `1.0.1`.

La rama de referencia para esta release es `prod`. El estado real del proyecto
en esta rama no es "solo SQLite": la aplicacion soporta dos modos de base de
datos mediante `DB_DB_TYPE`.

- `postgresql`: modo usado por Docker Compose y la ruta de produccion.
- `sqlite`: modo local de desarrollo, usado por defecto si no configuras
  `DB_DB_TYPE`.

Evidencia en el proyecto:

- `spider/config/settings.py` define `DB_DB_TYPE=sqlite|postgresql` y variables
  `DB_PGUSER`, `DB_PGPASSWORD`, `DB_PGDATABASE`, `DB_PGHOST`, `DB_PGPORT`.
- `spider/database/session.py` construye URIs `postgresql://...` o
  `sqlite:///...` segun `DB_DB_TYPE`.
- `api/main.py` usa `postgresql+asyncpg://` para sesiones async de FastAPI y
  `sqlite+aiosqlite://` para SQLite.
- `requirements.txt` incluye drivers PostgreSQL (`psycopg`, `psycopg-binary`,
  `psycopg2-binary`, `asyncpg`) y SQLite (`aiosqlite`).
- `docker-compose*.yml` levanta `postgres:16-alpine` y fuerza el backend a
  `DB_DB_TYPE=postgresql`.
- `humble_bundle.db` fue removido del tracking en `prod`; SQLite queda como
  fallback local, no como base principal de produccion.

## Stack

- Python 3.12+ / 3.13
- FastAPI + Uvicorn
- SQLAlchemy 2
- PostgreSQL 16 via Docker Compose para produccion
- SQLite + aiosqlite para desarrollo local opcional
- Requests + BeautifulSoup4 para scraping
- pandas + Pydantic + pydantic-settings para normalizacion y configuracion
- JWT con `python-jose`
- Password hashing con `bcrypt(SHA-256(password))`
- Vue 3 + Vite + TypeScript + Vue Router
- axios, vue-i18n, Sass, ESLint, Stylelint, Vitest

## Requisitos

1. Python 3.12 o 3.13 con `venv` y `pip`.
2. Node.js 20+ para desarrollo frontend.
3. Docker y Docker Compose para levantar la pila con PostgreSQL.
4. Acceso de red para ejecutar el ETL contra Humble Bundle.

## Configuracion

Copia el ejemplo y ajusta secretos antes de levantar servicios:

```bash
cp .env.example .env
```

Variables relevantes:

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

Notas:

- En Docker Compose, el servicio `api` fuerza `DB_DB_TYPE=postgresql` y usa el
  host interno `postgres`.
- Para ejecucion local sin Docker, puedes usar SQLite con
  `DB_DB_TYPE=sqlite` y `DB_DB_PATH=humble_bundle.db`.
- Para usar PostgreSQL fuera de Docker, define `DB_DB_TYPE=postgresql` y apunta
  `DB_PGHOST` al host real de tu base.

## Docker Compose

Ruta recomendada para `prod`:

```bash
docker compose up -d --build
```

Servicios:

- `postgres`: PostgreSQL 16 con volumen `postgres_data`.
- `api`: FastAPI en `http://localhost:5002`.
- `frontend`: Vite preview/container en `http://localhost:${FRONTEND_PORT:-3002}`.

Comandos Makefile:

- `make docker-up`: levanta `docker compose up -d`.
- `make docker-down`: detiene servicios y elimina volumenes con `down -v`.
- `make docker-restart`: reconstruye contenedores desde cero.

## Instalacion Local

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

Variables frontend:

```env
VITE_API_BASE_URL=http://localhost:5002
```

El cliente frontend usa `VITE_API_BASE_URL` si existe. Si no existe, cae a
`/humblebundlespider/api`, que sirve para despliegues bajo subruta/proxy.

## Comandos

- `make etl`: ejecuta `python -m spider.cli.run_spider`.
- `make api`: levanta FastAPI local en `http://0.0.0.0:5002`.
- `make db-init`: inicializa tablas usando la configuracion `DB_*` actual.
- `make db-reset`: elimina solo el archivo SQLite local `humble_bundle.db`.
- `make frontend-dev`: levanta Vite en `http://localhost:3002`.
- `make frontend-build`: ejecuta el build de produccion del frontend.
- `make docker-up`: levanta la pila Docker con PostgreSQL.
- `make docker-down`: detiene la pila Docker y borra volumenes.
- `make docker-restart`: recrea la pila Docker con build nuevo.

Importante: `make db-reset` no resetea PostgreSQL. Para limpiar la base Docker,
usa `make docker-down` o `docker compose down -v`.

## ETL

```bash
source .venv/bin/activate
python -m spider.cli.run_spider
# o
make etl
```

Flujo:

1. Descarga `https://www.humblebundle.com/books`.
2. Extrae `script#landingPage-json-data`.
3. Normaliza productos con pandas.
4. Convierte fechas, URLs, listas JSON y metricas derivadas.
5. Entra a cada pagina de bundle y lee `webpack-bundle-page-data`.
6. Extrae tiers, libros, MSRP total y HTML bruto.
7. Valida con Pydantic.
8. Elimina bundles expirados.
9. Persiste bundles por `machine_name` y guarda snapshots raw con hash.

## API

La metadata de FastAPI para esta release es `1.0.1`.

Endpoints publicos:

- `GET /health`: estado del servicio.
- `GET /bundles`: lista de bundles ordenada por fecha de cierre.
- `GET /bundles/{bundle_id}`: bundle por UUID.
- `GET /bundles/by-machine-name/{machine_name}`: bundle por `machine_name`.
- `GET /bundles/featured`: bundle destacado por MSRP y ventas.
- `GET /landing-page-raw-data`: snapshots raw.
- `GET /landing-page-raw-data/latest`: ultimo snapshot raw.
- `GET /landing-page-raw-data/{raw_data_id}`: snapshot raw por UUID.

Autenticacion:

- `POST /auth/login`: valida usuario y password, devuelve JWT.
- `GET /auth/me`: devuelve el usuario autenticado.

Endpoint protegido:

- `POST /etl/run`: ejecuta ETL y requiere `Authorization: Bearer <token>`.

## Autenticacion

El modelo `User` se persiste en la misma base configurada por `DB_DB_TYPE`.

Seed automatico:

1. Define `DB_ADMIN_USERNAME` y `DB_ADMIN_EMAIL`.
2. Define una de estas variables:
   - `DB_ADMIN_PASSWORD_PLAIN`: password en texto plano; el backend lo normaliza.
   - `DB_ADMIN_PASSWORD_SHA`: SHA-256 hex de 64 caracteres.
3. Al iniciar la API, `ensure_admin_user` crea o actualiza el usuario.

Comandos utiles:

```bash
python -m spider.cli.create_user \
  --username admin \
  --email admin@example.com \
  --password "admin_password"

python -m spider.cli.hash_password --password "admin_password"
```

Hashing:

- El backend normaliza passwords como `SHA-256(plain)` si recibe texto plano.
- El hash final almacenado es `bcrypt(SHA-256(password))`.
- `api/security.py` acepta texto plano o SHA-256 hex para operaciones manuales.

## Frontend

El frontend es una SPA Vue 3 con router:

- `/`: vista principal con bundles, utilidades y controles.
- `/login`: login contra `POST /auth/login`.

Caracteristicas:

- Tema claro/oscuro.
- Selector de idioma `es`/`en`.
- Sesion JWT en `localStorage`.
- Boton flotante de logout.
- Ejecucion de ETL desde UI cuando hay token.
- Vistas de bundles y raw data con utilidades para abrir/descargar JSON.

Scripts:

- `npm run dev`
- `npm run build`
- `npm run preview`
- `npm run lint`
- `npm run lint:style`
- `npm run test:run`
- `npm run test:coverage`

## Arquitectura

- `api/`: FastAPI, auth, schemas HTTP y endpoints.
- `spider/core/`: `HumbleSpider` y errores de dominio.
- `spider/scrapers/`: scraper de detalle por bundle.
- `spider/database/`: modelos SQLAlchemy, sesion, persistencia y seed de usuario.
- `spider/schemas/`: modelos Pydantic del ETL.
- `spider/config/`: settings `DB_*`.
- `spider/cli/`: comandos para ETL, creacion de usuario y hash de password.
- `frontend/src/views/`: vistas principales con Vue Router.
- `frontend/src/components/`: cards, utilidades, UI base y controles flotantes.
- `frontend/src/composables/`: auth, bundles, raw data, responsive y dark mode.
- `docker-compose*.yml`: pila PostgreSQL + API + frontend.
- `tests/`: pruebas backend de seguridad.

## Base De Datos

Tablas actuales:

- `bundle`: metadata normalizada, fechas, estado activo, tiers, libros,
  imagenes, MSRP y HTML bruto.
- `landing_page_raw_data`: snapshots del JSON `landingPage-json-data`, fecha,
  URL fuente, hash y version opcional.
- `user`: usuarios de autenticacion con `username`, `email`, `password_hash` y
  `created_at`.

Modo PostgreSQL:

- Activado con `DB_DB_TYPE=postgresql`.
- Requiere `DB_PGUSER`, `DB_PGPASSWORD` y `DB_PGDATABASE`.
- Usa `psycopg` para operaciones sync y `asyncpg` para FastAPI async.
- En Docker, `DB_PGHOST=postgres`.

Modo SQLite:

- Activado con `DB_DB_TYPE=sqlite`.
- Usa `DB_DB_PATH=humble_bundle.db`.
- Esta pensado para desarrollo local y pruebas rapidas.

## Verificacion

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
