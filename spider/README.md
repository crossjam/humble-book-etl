# Spider de Humble Bundle

Este paquete encapsula el scraper ETL que obtiene bundles de **Humble Bundle Books**, normaliza los datos, descarga el detalle de cada bundle y persiste todo en SQLite.

## Adaptadores de fuente (2026-11-12)

El sitio actual usa datos Next.js Flight embebidos. `scrapers/next_page.py`
decodifica frames JSON y texto UTF-8, sin ejecutar JavaScript, y resuelve las
referencias de datos de los tiers acumulativos. Los parsers legacy
`landingPage-json-data` / `webpack-bundle-page-data` siguen disponibles.
Los IDs provienen de `productKey` / `key`, nunca del slug; las fechas reales
vienen del detalle. Un listado Next exige detalles válidos para todos los bundles.

Antes de guardar snapshots, archivar o actualizar, tanto CLI como API rechazan
scrapes vacíos/duplicados o con caída de más del 50% respecto al baseline confiable.
Los datos omitidos/null no borran metadatos existentes; las listas explícitas
sí son autoritativas. La disponibilidad usa `start <= UTC now < end`, también
en las consultas públicas API/MCP aunque el ETL haya fallado.

Ver [contrato, fixtures y checklist de migración](../docs/humble-nextjs-migration.md).

## Estructura del Módulo

```
spider/
├── __init__.py              # Exporta clases/funciones principales
├── README.md                # Este archivo
│
├── cli/                     # Punto de entrada CLI
│   ├── __init__.py
│   └── run_spider.py        # Script ejecutable principal
│
├── core/                    # Lógica principal del spider
│   ├── __init__.py
│   ├── errors.py            # Excepciones personalizadas
│   └── spider.py            # Clase HumbleSpider
│
├── scrapers/                # Scrapers especializados
│   ├── __init__.py
│   ├── bundle_detail_scraper.py  # BundleDetailScraper (detalles de bundles)
│   └── next_page.py         # Adaptador versionado de Next.js Flight
│
├── schemas/                 # Modelos Pydantic
│   ├── __init__.py
│   ├── bundle.py            # BundleRecord
│   └── raw_data.py          # LandingPageRawDataRecord
│
├── database/                # Capa de persistencia
│   ├── __init__.py
│   ├── models.py            # Modelos SQLAlchemy (Bundle, LandingPageRawData)
│   ├── persistence.py       # Funciones de persistencia (persist_bundles, etc.)
│   └── session.py           # Fábrica de sesiones SQLAlchemy
│
├── config/                  # Configuración
│   ├── __init__.py
│   └── settings.py          # Settings (Pydantic Settings)
│
└── utils/                   # Utilidades y transformadores
    ├── __init__.py
    └── transformers.py      # Funciones de normalización y transformación
```

## Arquitectura General

El sistema implementa un pipeline ETL (Extract, Transform, Load) para obtener, normalizar y persistir bundles de Humble Bundle Books. La arquitectura se organiza en capas bien definidas:

1. **Extracción**: Obtiene datos del sitio web mediante scraping
2. **Transformación**: Normaliza y enriquece los datos
3. **Carga**: Persiste los datos en SQLite

### Componentes Principales

- **CLI** (`cli/run_spider.py`): Punto de entrada que orquesta el flujo completo
- **Core** (`core/spider.py`): Clase `HumbleSpider` que maneja la extracción y transformación
- **Scrapers** (`scrapers/bundle_detail_scraper.py`): Enriquecimiento de datos con detalles de cada bundle (tiers, libros, MSRP, tile_logo)
- **Schemas** (`schemas/`): Validación de datos con Pydantic (`BundleRecord`, `LandingPageRawDataRecord`)
- **Database** (`database/`): Modelos ORM, persistencia y gestión de sesiones SQLite
- **Config** (`config/settings.py`): Configuración basada en variables de entorno (SQLite)
- **Utils** (`utils/transformers.py`): Funciones auxiliares de transformación

## Flujo general

- **`cli/run_spider.py`**: punto de entrada. Carga configuración (`DB_*`), ejecuta `HumbleSpider`, archiva bundles expirados y guarda los nuevos/actualizados.
- **`core/HumbleSpider`**: hace GET a `https://www.humblebundle.com/books`, detecta el payload Next o legacy, normaliza campos con pandas y Pydantic (`BundleRecord`), y para cada bundle consulta el detalle con `BundleDetailScraper`.
- **`scrapers/BundleDetailScraper`**: descarga la página del bundle, lee el modelo Next correspondiente al slug (o `webpack-bundle-page-data` legacy), extrae fechas, tiers, libros, MSRP e imágenes.
- **Persistencia**: `database/persistence.py` valida calidad antes de escribir, hace upsert por `machine_name`, conserva metadatos desconocidos y archiva snapshots versionados.

## Modelo de Datos

### Tabla bundle

```
┌─────────────────────────────────────────────────────────────────┐
│                          BUNDLE                                 │
├─────────────────────────────────────────────────────────────────┤
│ PK  id                          VARCHAR (String)                │
│ UNQ machine_name               VARCHAR(255)  NOT NULL           │
│     high_res_tile_image         VARCHAR(2048)                   │
│     disable_hero_tile           BOOLEAN                         │
│     marketing_blurb             VARCHAR(1024)                   │
│     hover_title                 VARCHAR(255)                    │
│     product_url                 VARCHAR                          │
│     tile_image                  VARCHAR(2048)                   │
│     category                    VARCHAR(128)                    │
│     hero_highlights             VARCHAR                         │
│     hover_highlights            VARCHAR                         │
│     author                      VARCHAR(255)                    │
│     supports_partners           BOOLEAN                         │
│     detailed_marketing_blurb    VARCHAR(2048)                   │
│     tile_logo                   VARCHAR(2048)                   │
│     tile_short_name             VARCHAR(255)                    │
│     start_date_datetime         TIMESTAMP  (INDEX)              │
│     end_date_datetime           TIMESTAMP  (INDEX)              │
│     tile_stamp                  VARCHAR(64)                     │
│     bundles_sold_decimal        FLOAT                           │
│     tile_name                   VARCHAR(255)                    │
│     short_marketing_blurb       VARCHAR(512)                    │
│     _type                       VARCHAR                         │
│     highlights                  VARCHAR                         │
│     tile_logo_information_*     VARCHAR                         │
│     high_res_tile_image_info_*  VARCHAR                         │
│     tile_image_information_*    VARCHAR                         │
│     verification_date           TIMESTAMP  NOT NULL             │
│     duration_days               FLOAT                           │
│     is_active                   BOOLEAN  (INDEX)                │
│     archived_at                 TIMESTAMP (INDEX)                │
│     price_tiers                 TEXT (JSON)                    │
│     book_list                   TEXT (JSON)                    │
│     featured_image              VARCHAR                         │
│     msrp_total                  FLOAT                           │
│     raw_html                    TEXT                            │
└─────────────────────────────────────────────────────────────────┘
```

### Tabla landing_page_raw_data

```
┌────────────────────────────────────────────────┐
│             LANDING_PAGE_RAW_DATA              │
├────────────────────────────────────────────────┤
│ PK  id              VARCHAR (String)            │
│     json_data       TEXT (JSON)  NOT NULL      │
│     scraped_date    TIMESTAMP  (INDEX)         │
│     source_url      VARCHAR    NOT NULL        │
│     json_hash       VARCHAR    (INDEX)         │
│     json_version    VARCHAR                    │
└────────────────────────────────────────────────┘
```

La tabla `bundle` almacena los metadatos enriquecidos, `bundle_lifecycle_event` conserva cambios de calendario de forma append-only, y `landing_page_raw_data` guarda el JSON bruto para trazabilidad y auditoría.

### Tabla bundle_lifecycle_event

Cada evento conserva `machine_name`, el `bundle_id` cuando existe, tipo (`extended`, `renewed`, `shortened` o `reactivated`), fecha de observación, ventanas anterior/nueva y el snapshot fuente opcional. `event_key` evita duplicados durante reintentos y backfills.

## Flujo de Datos Completo

```
┌─────────────────────────────────────────────────────────────────────┐
│                        1. EXTRACCIÓN                                │
├─────────────────────────────────────────────────────────────────────┤
│ HumbleSpider.fetch_bundles()                                        │
│   └─> GET https://www.humblebundle.com/books                        │
│       └─> Detecta listado Next.js Flight o JSON legacy             │
│           └─> Parsea JSON → products[]                              │
└─────────────────────────────────────────────────────────────────────┘
                              ▼
┌─────────────────────────────────────────────────────────────────────┐
│                      2. TRANSFORMACIÓN                              │
├─────────────────────────────────────────────────────────────────────┤
│ HumbleSpider._normalize_products()                                  │
│   └─> pandas.json_normalize()                                       │
│       ├─> Convierte fechas a UTC                                    │
│       ├─> Serializa campos JSON                                     │
│       ├─> Normaliza texto (trim, nulls)                             │
│       ├─> Absolutiza URLs                                           │
│       └─> Calcula duration_days, is_active                          │
└─────────────────────────────────────────────────────────────────────┘
                              ▼
┌─────────────────────────────────────────────────────────────────────┐
│                      3. ENRIQUECIMIENTO                             │
├─────────────────────────────────────────────────────────────────────┤
│ HumbleSpider._to_records()                                          │
│   Para cada producto:                                               │
│     └─> BundleDetailScraper.fetch_bundle_details(product_url)       │
│         ├─> GET página del bundle                                   │
│         ├─> Extrae modelo Next.js Flight o JSON legacy             │
│         ├─> Parsea JSON embebido                                   │
│         ├─> Extrae price_tiers, metadatos detallados por título y msrp_total │
│         ├─> Normaliza tile_logo (si existe)                         │
│         └─> Guarda raw_html                                         │
└─────────────────────────────────────────────────────────────────────┘
                              ▼
┌─────────────────────────────────────────────────────────────────────┐
│                        4. VALIDACIÓN                                │
├─────────────────────────────────────────────────────────────────────┤
│ BundleRecord.model_validate(item)                                   │
│   └─> Pydantic valida tipos, longitudes, formatos                   │
│       └─> Registro inválido → aborta el scrape sin persistir        │
└─────────────────────────────────────────────────────────────────────┘
                              ▼
┌─────────────────────────────────────────────────────────────────────┐
│                          5. PERSISTENCIA                            │
├─────────────────────────────────────────────────────────────────────┤
│ persist_bundles(records, session)                                   │
│   ├─> SELECT/UPDATE en tabla 'bundle' (SQLite)                      │
│   └─> registra cambios de calendario en bundle_lifecycle_event      │
│                                                                      │
│ remove_outdated_bundles(session)                                    │
│   └─> archiva bundles donde end_date_datetime < NOW()              │
└─────────────────────────────────────────────────────────────────────┘
```

### Índices Principales

- `bundle.id` (PRIMARY KEY)
- `bundle.machine_name` (UNIQUE, INDEX)
- `bundle.start_date_datetime` (INDEX)
- `bundle.end_date_datetime` (INDEX)
- `bundle.is_active` (INDEX)
- `bundle.verification_date` (INDEX)
- `bundle.archived_at` (INDEX)
- `bundle_lifecycle_event.event_key` (UNIQUE, INDEX)
- `bundle_lifecycle_event.machine_name` (INDEX)
- `bundle_lifecycle_event.observed_at` (INDEX)
- `landing_page_raw_data.json_hash` (INDEX)

## Explicación por archivo

### CLI

- `cli/run_spider.py`: script ejecutable. Lee settings, instancia `HumbleSpider`, captura errores de fuente/calidad y valida el catálogo antes de cualquier escritura. Después guarda el snapshot, archiva expirados y persiste bundles. Crea sesiones usando `get_session_factory`.

### Core

- `core/spider.py`: clase `HumbleSpider`.
  - Constantes `URL`, `SCRIPT_ID`, listas de columnas JSON/fecha/texto.
  - `fetch_bundles()`: pipeline principal: obtiene payload, extrae productos, normaliza DataFrame, convierte a `BundleRecord`.
  - `get_raw_data_record()`: expone el último payload exitoso con hash y versión listo para persistir en `landing_page_raw_data`.
  - `_fetch_raw_payload()`: hace GET, detecta JSON legacy o Flight y levanta `HumbleSpiderError` si el payload no es válido.
  - `_extract_products()`: adapta `feedItems` Next o todos los mosaics legacy; rechaza catálogo vacío o identidades duplicadas.
  - `_normalize_products()`: usa pandas para limpiar, convertir fechas a UTC, serializar campos JSON, normalizar texto, absolutizar URLs y calcular `duration_days`/`is_active`.
  - `_to_records()`: obtiene detalle, fusiona fechas reales y enriquecimiento, recalcula disponibilidad/duración y valida con Pydantic. Cualquier registro inválido aborta el scrape; el formato Next exige detalle válido.
- `core/errors.py`: define excepciones de dominio `HumbleSpiderError`.

### Scrapers

- `scrapers/bundle_detail_scraper.py`: clase `BundleDetailScraper`.
  - `fetch_bundle_details(product_path)`: descarga y adapta datos Next o legacy; conserva tiers, libros, fechas e imágenes. Fallos de red/formato devuelven `None`, nunca un detalle vacío exitoso.
  - `_extract_price_tiers()`: extrae información de precios por tier desde el JSON.
  - `_extract_book_list()`: extrae `machine_name`, título, autores/creadores, editoriales, descripción, formatos, imágenes, MSRP, preview, tipo de contenido y tiers. Los datos provienen de `bundleData.tier_item_data`, el mismo payload que alimenta el panel de detalle abierto por los enlaces `href="#"` de la página.
  - `_safe_amount()`: extrae valores numéricos de objetos de dinero del JSON.
  - `next_page.py`: parser Flight limitado con presupuestos de tamaño/profundidad/expansión, texto UTF-8 por bytes, referencias exactas y paths React; no evalúa JavaScript.
  - Incluye dataclass `BundleDetails` con price_tiers, book_list, msrp_total, raw_html y fechas/imágenes opcionales.

### Schemas

- `schemas/bundle.py`: modelo Pydantic `BundleRecord`.
  - Define todos los campos del bundle con aliases (ej. `start_date|datetime`), longitudes máximas y tipos (`HttpUrl`, `datetime`, `float`).
  - Validadores: convierten highlights a string, controlan no negativos en `bundles_sold_decimal`/`duration_days`.
  - `to_orm_payload()`: adapta el diccionario para la capa ORM (cambia `type`→`_type`, cast de URL a string).
- `schemas/raw_data.py`: modelo Pydantic `LandingPageRawDataRecord`.
  - Guarda el payload de listado (legacy o Flight + productos) con `scraped_date`, `source_url`, hash y versión.
  - `to_orm_payload()`: devuelve el diccionario listo para persistir en `landing_page_raw_data`.

### Base de datos

- `database/models.py`: modelos SQLAlchemy.
  - `Bundle`: tabla principal con metadatos del bundle, tiers/libros en JSON, imagen destacada y HTML crudo.
  - `LandingPageRawData`: almacena el JSON bruto del script `landingPage-json-data` con hash y metadata de scraping.
- `database/session.py`: fábrica de sesión SQLite.
  - Construye URI con settings (ruta al archivo SQLite), crea directorio si no existe, crea la BD si no existe usando `Base.metadata.create_all(checkfirst=True)`.
  - Llama a `ensure_columns` y `ensure_landing_page_raw_data_table` para mantener el esquema mínimo.
- `database/persistence.py`: operaciones de persistencia y mantenimiento.
  - `persist_bundles`: SELECT/UPDATE en SQLite (busca por machine_name, actualiza o inserta).
  - `persist_landing_page_raw_data`: inserta el JSON bruto de landingPage con metadata.
  - `remove_outdated_bundles`: archiva bundles con `end_date_datetime` en el pasado sin eliminar sus metadatos; `archived_at` marca el período archivado actual y se reinicia si el bundle reaparece válidamente.
  - `recreate_database`: elimina el archivo SQLite si existe y recrea tablas y columnas.
  - `ensure_columns` y `ensure_landing_page_raw_data_table`: migraciones rápidas en SQL crudo para añadir columnas/tablas si faltan (usando tipos SQLite: TEXT, REAL, VARCHAR).

### Configuración

- `config/settings.py`: clase `Settings` (pydantic-settings) con prefijo `DB_` y `.env` opcional; contiene `db_path` (ruta al archivo SQLite) y `sql_echo`.

### Utilidades

- `utils/transformers.py`: helpers comunes.
  - `normalize_text`, `serialize_list`, `absolute_url`, `safe_float`.
  - Cálculo de `compute_duration_days` e `is_active` contra fechas UTC.
  - `normalize_columns` aplica `normalize_text` a columnas pandas especificadas.
- `utils/__init__.py`: exporta helpers.

### Paquete raíz

- `spider/__init__.py`: exporta clases/funciones principales para import fácil (`HumbleSpider`, modelos, schemas, settings, helpers de persistencia).

## Esquema de datos (SQLite)

- **bundle**: datos normalizados del listado + detalles (tiers, libros, tile_logo, HTML raw, flags `is_active`/`archived_at`/`duration_days`).
- **landing_page_raw_data**: snapshots del listado legacy o Flight con fecha, URL fuente, hash y versión; productos Next incluyen las fechas observadas en el detalle para replay.

**Nota**: Los tipos de datos usan SQLite (String en lugar de UUID, TEXT/JSON en lugar de JSONB, REAL en lugar de DOUBLE PRECISION).

## Ejecución local

1. Exporta variables o `.env` con `DB_DB_PATH` (valor por defecto: `humble_bundle.db`).
2. Inicializa la base de datos:

```bash
make db-init
```

3. Ejecuta el spider:

```bash
python -m spider.cli.run_spider
# o
make etl
```

El flujo creará tablas si no existen, archivará bundles expirados y hará upsert de los actuales en SQLite.

El payload de extracción puede almacenarse con `get_raw_data_record()` y
`persist_landing_page_raw_data()`, siempre después de `validate_scrape_quality()`.

## Consideraciones y limitaciones

- Los datos Flight son un contrato de aplicación no documentado por Humble. Cambios futuros pueden detener el ETL y requieren revisar el adaptador, no relajar sus validaciones.
- Los detalles se descargan secuencialmente. En Next cualquier fallo aborta el scrape; en legacy el detalle puede faltar sin borrar enriquecimiento previo.
- Todos los registros deben pasar Pydantic y tener fechas válidas. No se inventan fechas ni se anuncia éxito después de descartar registros.
- Una caída legítima de más del 50% requiere investigación del operador; no existe un bypass automático del control de calidad.
- Las migraciones de esquema se realizan con SQL crudo simple (`ensure_*`); no hay sistema de migraciones formal.

## Resumen de Características

### Ventajas del Sistema

1. **Arquitectura ETL Clara**: Separación bien definida entre extracción, transformación y carga
2. **Normalización Robusta**: Uso de pandas para limpieza y Pydantic para validación estricta
3. **Enriquecimiento de Datos**: Scraping detallado de cada bundle con resolución inteligente de URLs de imágenes
4. **Persistencia Eficiente**: UPSERT con ON CONFLICT para actualizar sin duplicados
5. **Trazabilidad**: Almacenamiento de HTML raw y snapshots del `landingPage-json-data` con hash para análisis posterior
6. **Configuración Flexible**: Settings basados en variables de entorno con valores por defecto sensatos
7. **Manejo de Errores**: Excepciones específicas del dominio y logging detallado

### Funcionalidades Clave

- **Extracción de Detalles**: Scraping del JSON embebido (`webpack-bundle-page-data`) para obtener tiers, libros y MSRP
- **Normalización de Datos**: Limpieza de texto, conversión de fechas, serialización JSON
- **Validación Estricta**: Validación Pydantic y aborto seguro ante registros inválidos
- **Mantenimiento Automático**: Limpieza de bundles expirados y actualización de columnas faltantes
- **SQLite Local**: Base de datos local sin dependencias externas

## Próximos pasos recomendados

- Añadir reintentos/backoff/paralelización al fetch de detalle.
- Ampliar los fixtures reducidos de `tests/fixtures/next` al cambiar la fuente.
- Migrar a un sistema de migraciones (Alembic) en lugar de SQL adhoc.
