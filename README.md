# Humble Scrape

Pipeline ETL + API FastAPI + frontend Vue para obtener bundles publicos de
[Humble Bundle Books](https://www.humblebundle.com/books), normalizarlos,
enriquecerlos con datos de detalle, guardarlos en SQLite y explorarlos desde una
interfaz web.

## Estado actual

Esta version del proyecto esta lista para etiquetarse como release `1.0.0`.

- Spider Python que lee `landingPage-json-data` desde `/books`, normaliza los
  productos con pandas y valida registros con Pydantic.
- Enriquecimiento por bundle desde `webpack-bundle-page-data`: tiers de precio,
  lista de libros, MSRP total, imagen destacada y HTML bruto para auditoria.
- Persistencia SQLite con SQLAlchemy: upsert de bundles por `machine_name`,
  limpieza de bundles expirados y snapshots del JSON bruto de landing page con
  hash SHA-256.
- API FastAPI declarada como `1.0.0` con endpoints de bundles, ejecucion ETL y
  consulta de raw data.
- Frontend Vue 3 + Vite + TypeScript con tema claro/oscuro, i18n `es`/`en`,
  vistas responsive desktop/mobile y utilidades para inspeccionar o descargar
  JSON de bundles y raw data.
- Tests frontend configurados con Vitest + Vue Test Utils + happy-dom. La
  cobertura actual esta enfocada en `LandingPageRawDataUtility`.

## Stack

- Python 3.12+ / 3.13
- Requests + BeautifulSoup4 para scraping
- pandas + Pydantic + pydantic-settings para normalizacion y configuracion
- SQLAlchemy 2 + SQLite + aiosqlite
- FastAPI + Uvicorn
- Vue 3 + Vite 6 + TypeScript
- axios, vue-i18n, Sass, ESLint, Stylelint, Vitest

## Requisitos

1. Python 3.12 o 3.13 con `venv` y `pip`.
2. Node.js 20+ para el frontend.
3. Acceso de red para ejecutar el ETL contra Humble Bundle.

## Instalacion backend

```bash
git clone <repo>
cd humbleBundle
python3 -m venv .venv
source .venv/bin/activate
pip install -r requirements.txt
make db-init
```

Variables opcionales en `.env`:

```env
DB_DB_PATH=humble_bundle.db
DB_SQL_ECHO=false
```

## Comandos principales

- `make etl`: ejecuta `python -m spider.cli.run_spider`.
- `make api`: levanta FastAPI en `http://0.0.0.0:5002`.
- `make db-init`: crea la base SQLite y sus tablas si faltan.
- `make db-reset`: elimina `humble_bundle.db`; luego ejecuta `make db-init`.
- `make frontend-dev`: levanta Vite en `http://localhost:3002`.
- `make frontend-build`: ejecuta el build de produccion del frontend.

## Ejecutar el ETL

```bash
source .venv/bin/activate
python -m spider.cli.run_spider
# o
make etl
```

El flujo hace lo siguiente:

1. Descarga la pagina publica de Humble Bundle Books.
2. Extrae el JSON embebido en `script#landingPage-json-data`.
3. Normaliza campos, fechas, URLs, listas JSON y metricas derivadas.
4. Consulta cada pagina de bundle para extraer tiers, libros, MSRP y HTML bruto.
5. Valida con Pydantic, elimina bundles expirados y persiste con upserts en
   SQLite.
6. Guarda un snapshot del JSON bruto de landing page para trazabilidad.

## API FastAPI

```bash
source .venv/bin/activate
uvicorn api.main:app --reload --host 0.0.0.0 --port 5002
# o
make api
```

Endpoints disponibles:

- `GET /health`: estado del servicio y ruta de base de datos.
- `GET /bundles`: lista completa ordenada por fecha de cierre.
- `GET /bundles/{bundle_id}`: detalle por UUID.
- `GET /bundles/by-machine-name/{machine_name}`: compatibilidad por
  `machine_name`.
- `GET /bundles/featured`: bundle destacado por MSRP total y ventas.
- `POST /etl/run`: dispara el spider, limpia expirados y persiste el resultado.
- `GET /landing-page-raw-data`: lista de snapshots raw.
- `GET /landing-page-raw-data/latest`: ultimo snapshot raw guardado.
- `GET /landing-page-raw-data/{raw_data_id}`: snapshot raw por UUID.

La API monta tambien `/images` para desarrollo local y crea los directorios
`images/bundles` e `images/books` si no existen.

## Frontend

```bash
cd frontend
npm install
npm run dev
```

Variable opcional:

```bash
VITE_API_BASE_URL=http://127.0.0.1:5002
```

Scripts utiles:

- `npm run dev`: Vite dev server en `http://localhost:3002`.
- `npm run build`: type-check con `vue-tsc` y build Vite.
- `npm run preview`: previsualiza el build.
- `npm run lint`: ESLint.
- `npm run lint:style`: Stylelint para Vue/CSS/SCSS.
- `npm run test:run`: suite Vitest en modo CI.
- `npm run test:coverage`: cobertura Vitest.

Caracteristicas actuales:

- Tema claro/oscuro con persistencia local.
- Selector de idioma `es`/`en`.
- Vista destacada y listado de bundles activos.
- Layout desktop/mobile con `useResponsiveQueryEvent`.
- Boton para ejecutar `/etl/run` desde la interfaz.
- Pestana de utilidades con visor/exportador JSON de bundles y snapshots raw.

## Arquitectura del repositorio

- `spider/`: modulo ETL.
  - `core/`: clase `HumbleSpider` y errores de dominio.
  - `scrapers/`: scraper de detalle por bundle.
  - `database/`: modelos SQLAlchemy, sesiones y persistencia.
  - `schemas/`: modelos Pydantic de bundles y raw data.
  - `utils/`: transformadores de texto, URLs, fechas y metricas.
  - `config/`: settings basados en variables `DB_*`.
  - `cli/`: entrypoint `run_spider.py`.
- `api/`: aplicacion FastAPI, dependencias sync/async y schemas de respuesta.
- `frontend/`: SPA Vue 3 + Vite con componentes, composables, i18n y tests.
- `docs/`: notas tecnicas de perfilado de datos y stack visual heredado.
- `Makefile`: automatizaciones locales sin Docker.

## Base de datos

El proyecto usa SQLite para desarrollo local. Por defecto escribe en
`humble_bundle.db`; puedes cambiarlo con `DB_DB_PATH`.

Tablas principales:

- `bundle`: metadatos normalizados, estado activo, fechas, tiers, libros,
  imagenes, MSRP y HTML bruto.
- `landing_page_raw_data`: snapshots del JSON `landingPage-json-data` con fecha,
  URL fuente, hash y version opcional.

Para recrear la base:

```bash
make db-reset
make db-init
```

## Verificacion

```bash
cd frontend
npm run test:run
npm run build
```

No hay suite automatizada de backend en el estado actual. Para validar el flujo
completo manualmente, levanta la API, ejecuta `POST /etl/run` o `make etl` y
revisa `/bundles` y `/landing-page-raw-data`.

## Release 1.0.0

Checklist de esta release:

- README actualizado contra el estado real del proyecto.
- API y frontend documentados con los endpoints y scripts actuales.
- Tag git local `1.0.0` apuntando al commit de documentacion de release.
