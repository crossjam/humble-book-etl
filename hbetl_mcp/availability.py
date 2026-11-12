"""Read-time availability checks shared by the API and MCP catalog.

Stored flags can lag behind the clock when scraping fails. Public discovery
therefore fails closed for unknown schedules and uses a half-open sale period:
``start <= now < end``. Database DateTime columns contain naive UTC values.
"""

from datetime import datetime, timezone

from sqlalchemy import and_

from spider.database.models import Bundle


def utc_naive(value: datetime) -> datetime:
    """Convert offset-aware timestamps to the database's naive UTC convention."""
    if value.tzinfo is None:
        return value
    return value.astimezone(timezone.utc).replace(tzinfo=None)


def active_bundle_filter(now: datetime):
    """SQL predicate for a confirmed, unarchived bundle currently on sale.

    Comparisons also exclude NULL start/end dates: an incomplete schedule
    cannot establish that a bundle is currently available.
    """
    now_db = utc_naive(now)
    return and_(
        Bundle.is_active.is_(True),
        Bundle.archived_at.is_(None),
        Bundle.start_date_datetime <= now_db,
        Bundle.end_date_datetime > now_db,
    )


def bundle_is_active(bundle: Bundle, now: datetime) -> bool:
    """In-memory equivalent of :func:`active_bundle_filter`."""
    now_db = utc_naive(now)
    return bool(
        bundle.is_active
        and bundle.archived_at is None
        and bundle.start_date_datetime is not None
        and bundle.end_date_datetime is not None
        and utc_naive(bundle.start_date_datetime) <= now_db
        and utc_naive(bundle.end_date_datetime) > now_db
    )
