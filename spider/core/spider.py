from __future__ import annotations

import hashlib
import json
import logging
from typing import Dict, List, Optional

import httpx
import pandas as pd
from bs4 import BeautifulSoup
from pydantic import ValidationError

from ..scrapers.bundle_detail_scraper import BundleDetailScraper
from ..scrapers.next_page import (
    FORMAT as NEXT_FORMAT,
    listing_products,
    normalize_listing_product,
    parse_next_page,
)
from ..schemas.bundle import BundleRecord
from ..schemas.raw_data import LandingPageRawDataRecord
from ..utils.transformers import (
    absolute_url,
    compute_duration_days,
    compute_is_active,
    normalize_columns,
    normalize_text,
    serialize_list,
    safe_float,
)
from .errors import HumbleSpiderError

logger = logging.getLogger(__name__)


class HumbleSpider:
    """
    Obtiene los bundles publicados en Humble Bundle Books y los serializa a BundleRecord.
    """

    URL = 'https://www.humblebundle.com/books'
    SCRIPT_ID = 'landingPage-json-data'
    JSON_COLUMNS = ('hero_highlights', 'hover_highlights', 'highlights')
    DATETIME_COLUMNS = ('start_date|datetime', 'end_date|datetime')
    TEXT_COLUMNS = (
        'machine_name',
        'marketing_blurb',
        'hover_title',
        'product_url',
        'category',
        'author',
        'tile_name',
        'tile_short_name',
        'tile_stamp',
        'short_marketing_blurb',
        'tile_logo',
    )

    def __init__(self, session: httpx.Client | None = None) -> None:
        """
        Inicializa el spider de Humble Bundle.

        Args:
            session: Cliente HTTPX reutilizable. Si es None, se crea un cliente
                con HTTP/2 habilitado.
        """
        self._owns_session = session is None
        self.session = session or httpx.Client(
            http2=True,
            follow_redirects=True,
            timeout=30.0,
        )
        self.detail_scraper = BundleDetailScraper(self.session)
        self._last_raw_payload: Optional[Dict] = None
        self._detail_failures: List[Dict[str, str]] = []

    def close(self) -> None:
        if self._owns_session:
            self.session.close()

    def __enter__(self) -> 'HumbleSpider':
        return self

    def __exit__(self, *_args) -> None:
        self.close()

    def fetch_bundles(self) -> List[BundleRecord]:
        """
        Obtiene y procesa todos los bundles disponibles de Humble Bundle.

        Realiza el scraping de la página de Humble Bundle, extrae los datos
        de los productos, los normaliza y los convierte en registros validados.

        Returns:
            Lista de BundleRecord con los bundles obtenidos y validados.

        Raises:
            HumbleSpiderError: Si hay un error al obtener o procesar los datos.
        """
        self._last_raw_payload = None
        self._detail_failures = []
        payload = self._fetch_raw_payload()
        products = self._extract_products(payload)
        is_next = payload.get('format') == NEXT_FORMAT
        try:
            frame = self._normalize_products(products, next_source=is_next)
            records = self._to_records(frame, require_details=is_next)
        except (TypeError, ValueError, AttributeError, RecursionError) as exc:
            raise HumbleSpiderError(f'No se pudo normalizar el catálogo: {exc}') from exc
        if is_next:
            # Retain actual detail dates alongside the untouched Flight stream,
            # so lifecycle replay has the same window as the persisted record.
            # A product whose detail failed is kept in the listing snapshot for
            # catalog-count baselines, but carries no inferred schedule.
            by_key = {record.machine_name: record for record in records}
            for product in payload['products']:
                product.pop('start_date|datetime', None)
                product.pop('end_date|datetime', None)
                record = by_key.get(product['productKey'])
                if record is None:
                    for date_field in (
                        'startDate', 'endDate',
                        'start_date|datetime', 'end_date|datetime',
                    ):
                        product.pop(date_field, None)
                    continue
                product['startDate'] = record.start_date_datetime.isoformat()
                product['endDate'] = record.end_date_datetime.isoformat()
            if self._detail_failures:
                payload['detail_failures'] = self.detail_failures
        self._last_raw_payload = payload
        return records

    @property
    def detail_failures(self) -> List[Dict[str, str]]:
        """Return isolated Next-format detail failures from the last scrape."""
        return [dict(failure) for failure in self._detail_failures]

    def _record_detail_failure(self, machine_name: str, reason: str) -> None:
        failure = {
            'machine_name': machine_name,
            'reason': (reason or 'Bundle detail unavailable or invalid')[:500],
        }
        self._detail_failures.append(failure)
        logger.warning(
            'Skipping bundle %s after detail validation failed: %s',
            machine_name,
            failure['reason'],
        )

    def get_raw_data_record(self) -> Optional[LandingPageRawDataRecord]:
        """
        Obtiene el raw data record del último payload obtenido.

        Crea un LandingPageRawDataRecord con el último payload exitoso
        (legacy o Flight), incluyendo hash, versión y metadata.

        Returns:
            LandingPageRawDataRecord con el JSON y metadata, o None si
            no se ha obtenido ningún payload aún.
        """
        if not self._last_raw_payload:
            return None

        # Calcular hash del JSON
        json_str = json.dumps(self._last_raw_payload,
                              sort_keys=True, ensure_ascii=False)
        json_hash = hashlib.sha256(json_str.encode('utf-8')).hexdigest()

        return LandingPageRawDataRecord(
            json_data=self._last_raw_payload,
            source_url=self.URL,
            json_hash=json_hash,
            json_version=self._last_raw_payload.get('format', 'humble-landing-v1'),
        )

    def _fetch_raw_payload(self) -> Dict:
        """
        Obtiene el payload JSON crudo desde la página de Humble Bundle.

        Detecta el JSON legacy o los frames Flight embebidos en el listado.

        Returns:
            Diccionario con los datos JSON extraídos de la página.

        Raises:
            HumbleSpiderError: Si no se puede obtener la página o el script
                con los datos no se encuentra.
        """
        try:
            response = self.session.get(self.URL, timeout=30)
            response.raise_for_status()
        except httpx.HTTPError as exc:
            logger.exception('Error consultando %s', self.URL)
            raise HumbleSpiderError(
                'No se pudo obtener la página de Humble Bundle') from exc

        soup = BeautifulSoup(response.text, 'html.parser')
        script_tag = soup.select_one(f'script#{self.SCRIPT_ID}')
        if script_tag and script_tag.string:
            try:
                payload = json.loads(script_tag.string)
                if not isinstance(payload, dict):
                    raise ValueError('El payload no es un objeto')
                return payload
            except (ValueError, TypeError, RecursionError) as exc:
                raise HumbleSpiderError('JSON inválido en el listado legacy') from exc
        try:
            page = parse_next_page(response.text)
            return {
                'format': NEXT_FORMAT,
                'flight': page.flight,
                'products': listing_products(page),
            }
        except (ValueError, TypeError, KeyError, RecursionError) as exc:
            raise HumbleSpiderError(
                f'No se pudo interpretar el listado de Humble Bundle: {exc}') from exc

    def _extract_products(self, payload: Dict) -> List[Dict]:
        """
        Extrae la lista de productos del payload JSON.

        Args:
            payload: Diccionario con los datos JSON obtenidos de la página.

        Returns:
            Lista de diccionarios, cada uno representando un producto/bundle.

        Raises:
            HumbleSpiderError: Si la estructura del JSON no es la esperada.
        """
        try:
            if payload.get('format') == NEXT_FORMAT:
                products = [normalize_listing_product(p) for p in payload['products']]
            else:
                products = [
                    product
                    for mosaic in payload['data']['books']['mosaic']
                    for product in mosaic['products']
                ]
            if not products or any(not isinstance(p, dict) for p in products):
                raise ValueError('El catálogo está vacío o contiene productos inválidos')
            keys = [p.get('machine_name') for p in products]
            if any(not key for key in keys) or len(set(keys)) != len(keys):
                raise ValueError('Identidades de bundle ausentes o duplicadas')
            return products
        except (KeyError, IndexError, TypeError, ValueError) as exc:
            raise HumbleSpiderError(
                'Estructura JSON inesperada al obtener productos') from exc

    def _normalize_products(
        self, products: List[Dict], *, next_source: bool = False,
    ) -> pd.DataFrame:
        """
        Normaliza y transforma los productos en un DataFrame de pandas.

        Realiza las siguientes transformaciones:
        - Convierte listas anidadas a JSON strings
        - Normaliza fechas a datetime UTC
        - Normaliza columnas de texto
        - Convierte URLs relativas a absolutas
        - Calcula duración en días y estado activo

        Args:
            products: Lista de diccionarios con datos de productos.

        Returns:
            DataFrame de pandas con los productos normalizados.
        """
        # The Next adapter already maps the listing onto flat schema fields.
        # Don't flatten dictionary-valued malformed scalars (for example a
        # dict in `description`) into dotted extra columns; Pydantic must see
        # and quarantine the bad field on its own bundle.
        frame = pd.json_normalize(products, max_level=0 if next_source else None)
        if frame.empty:
            return frame

        frame.replace('', None, inplace=True)
        frame.dropna(axis=1, how='all', inplace=True)

        for column in self.DATETIME_COLUMNS:
            if column in frame.columns and not next_source:
                frame[column] = pd.to_datetime(
                    frame[column], errors='coerce', utc=True)

        for column in self.JSON_COLUMNS:
            if column in frame.columns:
                frame[column] = frame[column].apply(serialize_list)

        if next_source:
            # Preserve malformed per-product values for BundleRecord validation
            # below. A list in one optional text field must not make pandas
            # abort normalization for every otherwise valid bundle.
            for column in self.TEXT_COLUMNS:
                if column in frame.columns:
                    frame[column] = frame[column].apply(
                        lambda value: normalize_text(value)
                        if isinstance(value, str) else value
                    )
        else:
            normalize_columns(frame, self.TEXT_COLUMNS)

        if 'product_url' in frame.columns:
            frame['product_url'] = frame['product_url'].apply(absolute_url)

        if 'bundles_sold|decimal' in frame.columns:
            frame['bundles_sold|decimal'] = frame['bundles_sold|decimal'].apply(
                safe_float)

        if next_source:
            # The authoritative Next schedule is read per-bundle below.
            frame['duration_days'] = None
            frame['is_active'] = False
        else:
            frame['duration_days'] = frame.apply(
                lambda row: compute_duration_days(
                    row.get('start_date|datetime'), row.get('end_date|datetime')),
                axis=1,
            )
            frame['is_active'] = frame.apply(
                lambda row: compute_is_active(
                    row.get('start_date|datetime'), row.get('end_date|datetime')),
                axis=1,
            )

        return frame

    def _to_records(self, frame: pd.DataFrame, *, require_details: bool = False) -> List[BundleRecord]:
        """
        Convierte el DataFrame en una lista de BundleRecord validados.

        Para cada producto, obtiene detalles adicionales (precios, libros, imágenes)
        mediante scraping de la página del bundle y valida los datos usando
        el schema BundleRecord.

        Args:
            frame: DataFrame con productos normalizados.

        Returns:
            Lista de BundleRecord validados. En el formato Next, un fallo
            aislado omite solo ese bundle; la calidad del catálogo completo
            se valida antes de cualquier escritura.
        """
        if frame.empty:
            return []
        records: List[BundleRecord] = []
        # pandas represents absent optional values as NaN/NaT; neither should
        # become a value supplied to Pydantic or replace stored metadata.
        frame = frame.astype(object).where(pd.notna(frame), None)
        for item in frame.to_dict(orient='records'):
            machine_name = item.get('machine_name')
            try:
                detail = self.detail_scraper.fetch_bundle_details(
                    item.get('product_url')
                )
            except (
                AttributeError, IndexError, KeyError, RecursionError,
                TypeError, ValueError,
            ) as exc:
                if not require_details:
                    raise
                self._record_detail_failure(machine_name, str(exc))
                continue
            if require_details and detail is None:
                self._record_detail_failure(
                    machine_name,
                    self.detail_scraper.last_error or 'Bundle detail unavailable or invalid',
                )
                continue
            if detail:
                item['price_tiers'] = detail.price_tiers
                item['book_list'] = detail.book_list
                item['msrp_total'] = detail.msrp_total
                # Guardar HTML raw para tests
                item['raw_html'] = detail.raw_html
                for field in ('featured_image', 'tile_logo', 'marketing_blurb'):
                    value = getattr(detail, field, None)
                    if value is not None:
                        item[field] = value
                for field, source in (
                    ('start_date|datetime', 'start_date'),
                    ('end_date|datetime', 'end_date'),
                ):
                    value = getattr(detail, source, None)
                    if value is not None:
                        item[field] = pd.to_datetime(value, utc=True, errors='coerce')
                # tile_logo ya viene del JSON inicial, pero verificar que esté normalizado
                if isinstance(item.get('tile_logo'), str) and item['tile_logo']:
                    item['tile_logo'] = absolute_url(item['tile_logo'])
                    logger.debug('tile_logo extraído para %s: %s', machine_name, item.get('tile_logo'))
            try:
                start = item.get('start_date|datetime')
                end = item.get('end_date|datetime')
                if start is None or end is None or pd.isna(start) or pd.isna(end) or start >= end:
                    raise ValueError('Fechas ausentes, inválidas o invertidas')
                item['duration_days'] = compute_duration_days(start, end)
                item['is_active'] = compute_is_active(start, end)
                record = BundleRecord.model_validate(item)
            except (
                ValidationError, ValueError, TypeError, AttributeError,
                RecursionError,
            ) as exc:
                if require_details:
                    self._record_detail_failure(machine_name, str(exc))
                    continue
                raise HumbleSpiderError(
                    f'Bundle inválido {machine_name}; no se guardó el catálogo: {exc}') from exc
            records.append(record)
        return records
