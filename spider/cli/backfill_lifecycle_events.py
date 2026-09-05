from ..config.settings import get_settings
from ..database.persistence import backfill_lifecycle_events_from_raw_data
from ..database.session import get_session_factory


def main() -> None:
    """Backfill bundle schedule changes from retained landing-page snapshots."""
    SessionFactory = get_session_factory(get_settings())
    with SessionFactory() as session:
        created = backfill_lifecycle_events_from_raw_data(session)
    print(f'Lifecycle events created: {created}')


if __name__ == '__main__':
    main()
