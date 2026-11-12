from __future__ import annotations

import json
import logging
import math
from dataclasses import dataclass
from datetime import datetime
from typing import Any, Dict, List, Optional
from urllib.parse import unquote, urlsplit

import httpx
from bs4 import BeautifulSoup

from .next_page import parse_next_page

logger = logging.getLogger(__name__)


@dataclass
class BundleDetails:
    """Detalles extraídos del JSON de una página de bundle."""
    price_tiers: List[Dict[str, Any]]
    book_list: List[Dict[str, Any]]
    msrp_total: Optional[float]
    raw_html: Optional[str] = None  # HTML raw del bundle
    start_date: str | datetime | None = None
    end_date: str | datetime | None = None
    featured_image: Optional[str] = None
    tile_logo: Optional[str] = None
    marketing_blurb: Optional[str] = None


class BundleDetailScraper:
    """
    Extrae tiers y metadatos detallados de cada título desde el JSON embebido
    en la página del bundle.
    """
    BASE_URL = 'https://www.humblebundle.com'

    def __init__(self, session: httpx.Client | None = None) -> None:
        """
        Inicializa el scraper de detalles de bundles.

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

    def close(self) -> None:
        if self._owns_session:
            self.session.close()

    def __enter__(self) -> 'BundleDetailScraper':
        return self

    def __exit__(self, *_args) -> None:
        self.close()

    def fetch_bundle_details(self, product_path: str | None) -> Optional[BundleDetails]:
        """
        Obtiene los detalles de un bundle desde su página.
        
        Extrae información del JSON embebido (webpack-bundle-page-data) sobre
        precios, títulos, autores, editoriales, formatos, descripciones y MSRP.
        
        Args:
            product_path: Ruta o URL del producto. Puede ser relativa o absoluta.
            
        Returns:
            BundleDetails con la información extraída o None si hay un error.
        """
        if not product_path:
            return None
        
        url = product_path if product_path.startswith('http') else f'{self.BASE_URL}{product_path}'
        try:
            response = self.session.get(url)
            response.raise_for_status()
        except httpx.HTTPError as exc:
            logger.warning('No se pudo obtener detalle del bundle %s: %s', product_path, exc)
            return None

        # Guardar HTML raw para tests
        raw_html = response.text
        
        soup = BeautifulSoup(response.text, 'html.parser')
        script = soup.find('script', id='webpack-bundle-page-data', type='application/json')
        if script is None:
            return self._extract_next_details(raw_html, product_path)
        if not script.string:
            logger.warning('Script webpack-bundle-page-data vacío para %s', product_path)
            return None

        try:
            data = json.loads(script.string)
            bundle_data = data['bundleData']
            if not isinstance(bundle_data, dict):
                raise ValueError('bundleData no es un objeto')
        except (ValueError, KeyError, TypeError) as exc:
            logger.warning('JSON inválido en detalle %s: %s', product_path, exc)
            return None
        
        tier_pricing = bundle_data.get('tier_pricing_data', {})
        tier_display = bundle_data.get('tier_display_data', {})
        tier_items = bundle_data.get('tier_item_data', {})
        if (
            not all(isinstance(value, dict) for value in (tier_pricing, tier_display, tier_items))
            or not tier_items
            or not all(isinstance(value, dict) for value in tier_items.values())
            or not all(isinstance(value, dict) for value in tier_pricing.values())
            or not all(isinstance(value, dict) for value in tier_display.values())
        ):
            logger.warning('Datos de tiers/libros inválidos o vacíos para %s', product_path)
            return None
        
        price_tiers = self._extract_price_tiers(tier_pricing, tier_display)
        book_list = self._extract_book_list(tier_items, tier_display)
        
        basic_data = bundle_data.get('basic_data', {})
        if not isinstance(basic_data, dict):
            logger.warning('Metadatos básicos inválidos para %s', product_path)
            return None
        msrp_total = self._safe_amount(basic_data.get('msrp|money'))
        
        return BundleDetails(
            price_tiers=price_tiers,
            book_list=book_list,
            msrp_total=msrp_total,
            raw_html=raw_html,
        )

    def _extract_next_details(
        self, raw_html: str, product_path: str
    ) -> Optional[BundleDetails]:
        """Adapt the public Next.js model; never infer dates or item identities."""
        try:
            page = parse_next_page(raw_html)
            slug = unquote(urlsplit(product_path).path.rstrip('/').rsplit('/', 1)[-1])
            bundle = None
            for obj in page.objects():
                if not {'startDate', 'endDate', 'tiers', 'slug'}.issubset(obj):
                    continue
                # Recommendation objects can coexist with the requested bundle.
                if page.resolve(obj['slug']) == slug:
                    bundle = page.resolve(obj)
                    break
            if bundle is None:
                raise ValueError('No se encontró el detalle del slug solicitado')
            for field in ('startDate', 'endDate'):
                value = bundle.get(field)
                if not isinstance(value, str) or not value:
                    raise ValueError('Fechas de detalle ausentes o inválidas')
                parsed_date = datetime.fromisoformat(value)
                if parsed_date.tzinfo is None or parsed_date.utcoffset() is None:
                    raise ValueError('Fecha de detalle sin zona horaria')
            source_tiers = bundle['tiers']
            if not isinstance(source_tiers, list) or not source_tiers:
                raise ValueError('Tiers ausentes o inválidos')
            price_tiers = []
            books: Dict[str, Dict[str, Any]] = {}
            tier_ids = set()
            for tier in source_tiers:
                if not isinstance(tier, dict):
                    raise ValueError('Tier no es un objeto')
                # SKU is a source-provided stable identifier, not a slug-derived ID.
                identifier = tier.get('sku') or tier.get('id')
                if not isinstance(identifier, (str, int)) or isinstance(identifier, bool):
                    raise ValueError('Tier sin identificador')
                identifier = str(identifier)
                if not identifier or identifier in tier_ids:
                    raise ValueError('Identificador de tier vacío o duplicado')
                tier_ids.add(identifier)
                price = self._normalize_money(tier.get('price'))
                currency = price.get('currency') if price else None
                if (
                    price is None
                    or price['amount'] < 0
                    or not isinstance(currency, str)
                    or len(currency) != 3
                    or not currency.isascii()
                    or not currency.isalpha()
                ):
                    raise ValueError('Precio de tier ausente o inválido')
                items = tier.get('bundleItems')
                if not isinstance(items, list) or not items:
                    raise ValueError('Libros de tier ausentes o inválidos')
                machines = []
                for item in items:
                    if not isinstance(item, dict):
                        raise ValueError('Libro de tier no es un objeto')
                    machine = item.get('key')
                    if not isinstance(machine, str) or not machine.strip():
                        raise ValueError('Libro sin clave estable')
                    title = item.get('title')
                    if not isinstance(title, str) or not title.strip():
                        raise ValueError('Libro sin título')
                    if machine not in machines:
                        machines.append(machine)
                    if machine not in books:
                        books[machine] = self._next_book(item)
                    membership = books[machine]['tiers']
                    if identifier not in membership:
                        membership.append(identifier)
                price_tiers.append({
                    'identifier': identifier,
                    'price': price,
                    'average_purchase_price': self._normalize_money(
                        tier.get('averagePurchasePrice')
                    ),
                    'is_initial': tier.get('isInitialTier'),
                    'header': tier.get('header'),
                    'items': machines,
                })
            total_items = bundle.get('totalItems')
            if total_items is not None and (
                type(total_items) is not int or total_items != len(books)
            ):
                raise ValueError('Cantidad total de libros no coincide con el detalle')
            return BundleDetails(
                price_tiers=price_tiers,
                book_list=list(books.values()),
                msrp_total=self._safe_amount(bundle.get('value')),
                raw_html=raw_html,
                start_date=bundle['startDate'],
                end_date=bundle['endDate'],
                featured_image=bundle.get('thumbnailImageUrl'),
                tile_logo=bundle.get('logoImageUrl'),
                marketing_blurb=self._plain_text(bundle.get('headline')),
            )
        except (ValueError, KeyError, TypeError, IndexError, RecursionError) as exc:
            logger.warning('Detalle Next.js inválido para %s: %s', product_path, exc)
            return None

    def _next_book(self, item: Dict[str, Any]) -> Dict[str, Any]:
        developers = item.get('developers')
        source_publishers = item.get('publishers')
        platforms = item.get('platforms')
        for entries in (developers, source_publishers, platforms):
            if entries is not None and not isinstance(entries, list):
                raise ValueError('Metadatos de libro deben ser listas o null')
        formats = list(dict.fromkeys(
            value for value in platforms if isinstance(value, str)
        )) if platforms is not None else None
        publishers = [
            {'publisher-name': publisher.get('name'), 'publisher-url': publisher.get('url')}
            for publisher in (source_publishers or [])
            if isinstance(publisher, dict)
        ]
        preview = item.get('previewPdf')
        return {
            'machine_name': item['key'],
            'title': item.get('title'),
            # null means unavailable metadata; [] is an authoritative empty list.
            'authors': self._extract_people(developers, 'name') if developers is not None else None,
            'publishers': self._extract_publishers(publishers) if source_publishers is not None else None,
            'description': self._plain_text(item.get('description')),
            'msrp': self._safe_amount(item.get('msrp')),
            'preview': preview if isinstance(preview, dict) else None,
            'image': item.get('thumbnailImageUrl'),
            'detail_image': None,
            'content_type': item.get('bundleType'),
            'formats': formats,
            'tiers': [],
        }

    @staticmethod
    def _extract_price_tiers(pricing, display) -> List[Dict[str, Any]]:
        """
        Extrae los tiers de precios del JSON.
        
        Args:
            pricing: Diccionario con información de precios por tier
            display: Diccionario con información de visualización de los tiers
            
        Returns:
            Lista de diccionarios con información de cada tier
        """
        tiers: List[Dict[str, Any]] = []
        for identifier, info in pricing.items():
            tiers.append(
                {
                    'identifier': identifier,
                    'price': info.get('price|money'),
                    'average_purchase_price': info.get('average_purchase_price|money'),
                    'is_initial': info.get('is_initial_tier'),
                    'header': display.get(identifier, {}).get('header'),
                    'items': display.get(identifier, {}).get('tier_item_machine_names', []),
                }
            )
        return tiers

    def _extract_book_list(self, tier_items, display) -> List[Dict[str, Any]]:
        """
        Extrae la lista de libros del bundle desde los datos del JSON.
        
        Construye la lista de libros con sus metadatos, precios, imágenes y tiers.
        
        Args:
            tier_items: Diccionario con información de los items por tier
            display: Diccionario con información de visualización de los tiers
                
        Returns:
            Lista de diccionarios con metadatos detallados por título.
        """
        membership: Dict[str, List[str]] = {}
        for tier_id, data in display.items():
            for machine in data.get('tier_item_machine_names', []):
                membership.setdefault(machine, []).append(tier_id)

        books: List[Dict[str, Any]] = []
        for machine_name, info in tier_items.items():
            resolved_paths = info.get('resolved_paths') or {}
            description_html = info.get('description_text')
            books.append(
                {
                    'machine_name': machine_name,
                    'title': info.get('human_name'),
                    'authors': self._extract_people(
                        info.get('developers'), 'developer-name'
                    ),
                    'publishers': self._extract_publishers(info.get('publishers')),
                    'description': self._plain_text(description_html),
                    'msrp': BundleDetailScraper._safe_amount(info.get('msrp_price')),
                    'preview': info.get('book_preview'),
                    'image': resolved_paths.get('front_page_art_imgix'),
                    'detail_image': resolved_paths.get('preview_image'),
                    'content_type': info.get('item_content_type'),
                    'formats': self._extract_formats(info.get('platforms_and_oses')),
                    'tiers': membership.get(machine_name, []),
                }
            )
        return books

    @staticmethod
    def _extract_people(
        entries: Optional[List[Dict[str, Any]]], key: str
    ) -> List[str]:
        """Normalize Humble's comma-separated creator display names."""
        names: List[str] = []
        for entry in entries or []:
            value = entry.get(key) if isinstance(entry, dict) else None
            if not isinstance(value, str):
                continue
            for name in value.split(','):
                normalized = name.strip()
                if normalized and normalized not in names:
                    names.append(normalized)
        return names

    @staticmethod
    def _extract_publishers(
        entries: Optional[List[Dict[str, Any]]]
    ) -> List[Dict[str, Optional[str]]]:
        publishers: List[Dict[str, Optional[str]]] = []
        for entry in entries or []:
            if not isinstance(entry, dict):
                continue
            name = entry.get('publisher-name')
            url = entry.get('publisher-url')
            if name or url:
                publishers.append({'name': name, 'url': url})
        return publishers

    @staticmethod
    def _extract_formats(platforms: Optional[Dict[str, Any]]) -> List[str]:
        formats: List[str] = []
        for platform in (platforms or {}).values():
            if not isinstance(platform, dict):
                continue
            for values in platform.values():
                if not isinstance(values, list):
                    continue
                for value in values:
                    if isinstance(value, str) and value not in formats:
                        formats.append(value)
        return formats

    @staticmethod
    def _plain_text(value: Any) -> Optional[str]:
        if not isinstance(value, str) or not value.strip():
            return None
        return BeautifulSoup(value, 'html.parser').get_text(' ', strip=True)

    @staticmethod
    def _safe_amount(value: Optional[Dict[str, Any]]) -> Optional[float]:
        """
        Extrae el valor numérico de un objeto de dinero del JSON.
        
        Args:
            value: Diccionario con información de dinero (ej: {'amount': 29.99, 'currency': 'USD'})
            
        Returns:
            Valor numérico como float o None si no se puede extraer
        """
        money = BundleDetailScraper._normalize_money(value)
        return money['amount'] if money else None

    @staticmethod
    def _normalize_money(value: Any) -> Optional[Dict[str, Any]]:
        """`amount` is already major units; only `centAmount` needs scaling."""
        if not isinstance(value, dict):
            return None
        amount = value.get('amount', value.get('centAmount'))
        if isinstance(amount, bool):
            return None
        try:
            amount = float(amount)
            if 'amount' not in value and 'centAmount' in value:
                digits = value.get('fractionDigits', 2)
                if isinstance(digits, bool) or not isinstance(digits, int) or not 0 <= digits <= 9:
                    return None
                amount /= 10 ** digits
            if not math.isfinite(amount):
                return None
            return {
                'amount': amount,
                'currency': value.get('currency') or value.get('currencyCode'),
            }
        except (TypeError, ValueError, OverflowError):
            return None

