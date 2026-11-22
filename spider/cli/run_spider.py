import sys
from ..core.spider import HumbleSpider
from ..core.errors import HumbleSpiderError
from ..config.settings import get_settings
from ..database.session import get_session_factory
from ..database.persistence import (
    persist_bundles,
    remove_outdated_bundles,
    persist_landing_page_raw_data,
    ScrapeQualityError,
    get_detail_warning_bundle_ids,
    get_observed_bundle_count,
    get_skipped_bundle_ids,
    validate_scrape_quality,
)


def main() -> None:
    """
    Punto de entrada principal para ejecutar el spider de Humble Bundle.
    
    Obtiene los bundles desde Humble Bundle, limpia los bundles expirados
    de la base de datos y persiste los nuevos bundles obtenidos.
    
    Raises:
        SystemExit: Si ocurre un error al ejecutar el spider.
    """
    settings = get_settings()
    try:
        with HumbleSpider() as spider:
            print('Iniciando HumbleSpider...')
            records = spider.fetch_bundles()
            raw_data_record = spider.get_raw_data_record()
    except HumbleSpiderError as exc:
        raise SystemExit(f'Error ejecutando el spider: {exc}') from exc

    skipped_bundle_ids = get_skipped_bundle_ids(raw_data_record)
    warning_bundle_ids = get_detail_warning_bundle_ids(raw_data_record)
    try:
        bundles_seen = get_observed_bundle_count(
            records, raw_data_record, skipped_bundle_ids,
        )
    except ScrapeQualityError as exc:
        raise SystemExit(f'Error de calidad del scrape: {exc}') from exc
    print(f'Bundles obtenidos: {len(records)} de {bundles_seen}')
    if skipped_bundle_ids:
        print(
            f'Detalles omitidos ({len(skipped_bundle_ids)}); se conservan los '
            'registros guardados anteriormente: '
            + ', '.join(skipped_bundle_ids)
        )
    if warning_bundle_ids:
        print(
            f'Discrepancias toleradas ({len(warning_bundle_ids)}): '
            + ', '.join(warning_bundle_ids)
        )

    SessionFactory = get_session_factory(settings)
    with SessionFactory() as session:
        try:
            validate_scrape_quality(
                records, session,
                now=raw_data_record.scraped_date if raw_data_record else None,
                observed_count=bundles_seen,
                skipped_bundle_ids=skipped_bundle_ids,
            )
        except ScrapeQualityError as exc:
            raise SystemExit(f'Error de calidad del scrape: {exc}') from exc

        raw_data = None
        if raw_data_record:
            print('Persistiendo raw data de landingPage...')
            raw_data = persist_landing_page_raw_data(raw_data_record, session)

        print('Limpiando bundles expirados...')
        remove_outdated_bundles(
            session,
            now=raw_data_record.scraped_date if raw_data_record else None,
            exclude_machine_names=skipped_bundle_ids,
        )
        print('Persistiendo bundles...')
        persist_bundles(
            records,
            session,
            now=raw_data_record.scraped_date if raw_data_record else None,
            source_snapshot_id=raw_data.id if raw_data else None,
        )

        if raw_data_record and raw_data_record.json_data.get('detail_failures'):
            print('¡Proceso completado con omisiones parciales!')
        else:
            print('¡Proceso completado exitosamente!')


if __name__ == '__main__':
    main()
