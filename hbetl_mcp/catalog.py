"""Read-only catalog access for the Humble Book ETL MCP server."""

from __future__ import annotations

from contextlib import contextmanager
from datetime import datetime
from typing import Any, Iterator

from pydantic import BaseModel, ConfigDict, Field
from sqlalchemy import and_, nulls_last, or_, select
from sqlalchemy.orm import Session, sessionmaker

from spider.config.settings import Settings, get_settings
from spider.database.models import Bundle
from spider.database.session import build_database_uri
from sqlalchemy import create_engine


class Publisher(BaseModel):
    """Publisher metadata embedded in a bundle's book record."""

    model_config = ConfigDict(extra="allow")

    name: str
    url: str | None = None


class BookMetadata(BaseModel):
    """Normalized view of one book entry stored in ``Bundle.book_list``."""

    model_config = ConfigDict(extra="allow")

    machine_name: str
    title: str
    authors: list[str] = Field(default_factory=list)
    publishers: list[Publisher] = Field(default_factory=list)
    description: str | None = None
    msrp: float | str | None = None
    preview: dict[str, Any] = Field(default_factory=dict)
    image: str | None = None
    detail_image: str | None = None
    content_type: str | None = None
    formats: list[str] = Field(default_factory=list)
    tiers: list[str] = Field(default_factory=list)


class BundleSummary(BaseModel):
    """Compact bundle metadata suitable for discovery results."""

    id: str
    machine_name: str
    title: str | None = None
    category: str | None = None
    product_url: str | None = None
    start_date: datetime | None = None
    end_date: datetime | None = None
    is_active: bool = False
    book_count: int = 0


class BundleDetails(BundleSummary):
    """Bundle metadata with optional book and pricing details."""

    tile_short_name: str | None = None
    tile_stamp: str | None = None
    author: str | None = None
    msrp_total: float | None = None
    price_tiers: list[dict[str, Any]] | None = None
    books: list[BookMetadata] = Field(default_factory=list)


class BookSearchResult(BookMetadata):
    """Book metadata annotated with its containing bundle."""

    bundle_id: str
    bundle_machine_name: str
    bundle_title: str | None = None
    bundle_is_active: bool = False


def _as_list(value: Any) -> list[Any]:
    if value is None:
        return []
    if isinstance(value, list):
        return value
    return [value]


def _book_from_record(record: Any) -> BookMetadata:
    if not isinstance(record, dict):
        raise ValueError("Bundle book metadata is not an object")

    publishers = []
    for publisher in _as_list(record.get("publishers")):
        if isinstance(publisher, dict) and publisher.get("name"):
            publishers.append(publisher)
        elif isinstance(publisher, str):
            publishers.append({"name": publisher})

    payload = dict(record)
    payload["authors"] = [str(author) for author in _as_list(record.get("authors")) if author]
    payload["publishers"] = publishers
    payload["formats"] = [str(value) for value in _as_list(record.get("formats")) if value]
    payload["tiers"] = [str(value) for value in _as_list(record.get("tiers")) if value]
    payload["preview"] = record.get("preview") if isinstance(record.get("preview"), dict) else {}
    payload["title"] = str(record.get("title") or record.get("machine_name") or "Untitled")
    payload["machine_name"] = str(record.get("machine_name") or payload["title"])
    return BookMetadata.model_validate(payload)


def _books(bundle: Bundle) -> list[BookMetadata]:
    return [_book_from_record(record) for record in _as_list(bundle.book_list)]


def _summary(bundle: Bundle) -> BundleSummary:
    return BundleSummary(
        id=bundle.id,
        machine_name=bundle.machine_name,
        title=bundle.tile_name or bundle.tile_short_name,
        category=bundle.category,
        product_url=bundle.product_url,
        start_date=bundle.start_date_datetime,
        end_date=bundle.end_date_datetime,
        is_active=bool(bundle.is_active and bundle.archived_at is None),
        book_count=len(_as_list(bundle.book_list)),
    )


def _details(bundle: Bundle, include_books: bool = True) -> BundleDetails:
    summary = _summary(bundle)
    return BundleDetails(
        **summary.model_dump(),
        tile_short_name=bundle.tile_short_name,
        tile_stamp=bundle.tile_stamp,
        author=bundle.author,
        msrp_total=bundle.msrp_total,
        price_tiers=bundle.price_tiers,
        books=_books(bundle) if include_books else [],
    )


class BundleCatalog:
    """Read-only SQLAlchemy access to bundle and embedded book metadata."""

    def __init__(self, settings: Settings | None = None):
        self.settings = settings or get_settings()
        uri = build_database_uri(self.settings)
        connect_args = {"check_same_thread": False} if self.settings.db_type == "sqlite" else {}
        self.engine = create_engine(uri, future=True, connect_args=connect_args)
        self.session_factory = sessionmaker(
            bind=self.engine,
            expire_on_commit=False,
            class_=Session,
        )

    @contextmanager
    def session(self) -> Iterator[Session]:
        with self.session_factory() as session:
            yield session

    def list_bundles(
        self,
        query: str | None = None,
        include_inactive: bool = False,
        limit: int = 20,
        offset: int = 0,
    ) -> list[BundleSummary]:
        if not 1 <= limit <= 100:
            raise ValueError("limit must be between 1 and 100")
        if offset < 0:
            raise ValueError("offset must be non-negative")

        statement = select(Bundle)
        if not include_inactive:
            statement = statement.where(Bundle.is_active.is_(True), Bundle.archived_at.is_(None))
        if query:
            pattern = f"%{query.strip()}%"
            statement = statement.where(
                or_(
                    Bundle.machine_name.ilike(pattern),
                    Bundle.tile_name.ilike(pattern),
                    Bundle.tile_short_name.ilike(pattern),
                    Bundle.category.ilike(pattern),
                    Bundle.author.ilike(pattern),
                )
            )
        statement = statement.order_by(
            nulls_last(Bundle.end_date_datetime.desc()),
            Bundle.id.desc(),
        ).offset(offset).limit(limit)
        with self.session() as session:
            return [_summary(bundle) for bundle in session.scalars(statement).all()]

    def get_bundle(self, identifier: str, include_books: bool = True) -> BundleDetails:
        with self.session() as session:
            bundle = session.get(Bundle, identifier)
            if bundle is None:
                bundle = session.scalar(select(Bundle).where(Bundle.machine_name == identifier))
            if bundle is None:
                raise ValueError(f"Bundle not found: {identifier}")
            return _details(bundle, include_books=include_books)

    def featured_bundle(self) -> BundleDetails:
        statement = (
            select(Bundle)
            .where(Bundle.is_active.is_(True), Bundle.archived_at.is_(None))
            .order_by(nulls_last(Bundle.msrp_total.desc()), nulls_last(Bundle.bundles_sold_decimal.desc()))
            .limit(1)
        )
        with self.session() as session:
            bundle = session.scalar(statement)
            if bundle is None:
                raise ValueError("No active bundles stored")
            return _details(bundle)

    def search_books(
        self,
        query: str,
        author: str | None = None,
        publisher: str | None = None,
        bundle_identifier: str | None = None,
        include_inactive: bool = False,
        limit: int = 20,
    ) -> list[BookSearchResult]:
        query = query.strip().casefold()
        if not query:
            raise ValueError("query must not be empty")
        if not 1 <= limit <= 100:
            raise ValueError("limit must be between 1 and 100")

        with self.session() as session:
            if bundle_identifier:
                bundle = session.get(Bundle, bundle_identifier)
                if bundle is None:
                    bundle = session.scalar(select(Bundle).where(Bundle.machine_name == bundle_identifier))
                bundles = [bundle] if bundle is not None else []
            else:
                statement = select(Bundle).order_by(
                    nulls_last(Bundle.end_date_datetime.desc()), Bundle.id.desc()
                )
                if not include_inactive:
                    statement = statement.where(Bundle.is_active.is_(True), Bundle.archived_at.is_(None))
                bundles = list(session.scalars(statement).all())

        author_filter = author.casefold() if author else None
        publisher_filter = publisher.casefold() if publisher else None
        results: list[BookSearchResult] = []
        for bundle in bundles:
            bundle_title = bundle.tile_name or bundle.tile_short_name
            for raw_book in _as_list(bundle.book_list):
                book = _book_from_record(raw_book)
                haystack = " ".join(
                    [
                        book.machine_name,
                        book.title,
                        *book.authors,
                        *(publisher.name for publisher in book.publishers),
                        book.description or "",
                        book.content_type or "",
                    ]
                ).casefold()
                if query not in haystack:
                    continue
                if author_filter and not any(author_filter in value.casefold() for value in book.authors):
                    continue
                if publisher_filter and not any(
                    publisher_filter in value.name.casefold() for value in book.publishers
                ):
                    continue
                results.append(
                    BookSearchResult(
                        **book.model_dump(),
                        bundle_id=bundle.id,
                        bundle_machine_name=bundle.machine_name,
                        bundle_title=bundle_title,
                        bundle_is_active=bool(bundle.is_active and bundle.archived_at is None),
                    )
                )
                if len(results) >= limit:
                    return results
        return results

    def get_book(
        self,
        identifier: str,
        bundle_identifier: str | None = None,
        include_inactive: bool = False,
    ) -> BookSearchResult:
        matches = self.search_books(
            query=identifier,
            bundle_identifier=bundle_identifier,
            include_inactive=include_inactive,
            limit=100,
        )
        exact = [
            match
            for match in matches
            if match.machine_name.casefold() == identifier.casefold()
            or match.title.casefold() == identifier.casefold()
        ]
        if not exact:
            raise ValueError(f"Book not found: {identifier}")
        if bundle_identifier is None and len(exact) > 1:
            raise ValueError(
                f"Book identifier is ambiguous across bundles: {identifier}; provide bundle_identifier"
            )
        return exact[0]
