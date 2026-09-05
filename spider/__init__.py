"""Humble Bundle scraping, processing, and persistence package."""

from .config.settings import Settings, get_settings
from .core.errors import HumbleSpiderError
from .core.spider import HumbleSpider
from .database.models import Base, Bundle, BundleLifecycleEvent, LandingPageRawData
from .database.persistence import (
    backfill_lifecycle_events_from_raw_data,
    ensure_bundle_lifecycle_event_table,
    ensure_columns,
    ensure_landing_page_raw_data_table,
    persist_bundles,
    persist_landing_page_raw_data,
    recreate_database,
    remove_outdated_bundles,
)
from .database.session import build_database_uri, get_session_factory
from .schemas.bundle import BundleRecord
from .schemas.raw_data import LandingPageRawDataRecord

__all__ = [
    'HumbleSpider',
    'HumbleSpiderError',
    'Base',
    'Bundle',
    'BundleLifecycleEvent',
    'LandingPageRawData',
    'get_session_factory',
    'build_database_uri',
    'persist_bundles',
    'backfill_lifecycle_events_from_raw_data',
    'ensure_bundle_lifecycle_event_table',
    'remove_outdated_bundles',
    'recreate_database',
    'ensure_columns',
    'persist_landing_page_raw_data',
    'ensure_landing_page_raw_data_table',
    'BundleRecord',
    'LandingPageRawDataRecord',
    'Settings',
    'get_settings',
]
