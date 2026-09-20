"""Click entry point for the Humble Book ETL API client."""

from __future__ import annotations

import json
import os
from pathlib import Path
from typing import Any

import click

from . import __version__
from .client import ApiClient, ApiError
from .config import Config, default_config_path, load_config, save_config


class Context:
    def __init__(self, config: Config, config_path):
        self.config = config
        self.config_path = config_path


def dump_json(value: Any) -> None:
    click.echo(json.dumps(value, indent=2, ensure_ascii=False, default=str))


def render(value: Any, *, as_json: bool) -> None:
    if as_json:
        dump_json(value)
        return
    if isinstance(value, list):
        if not value:
            click.echo("No results.")
            return
        if all(isinstance(item, dict) for item in value):
            keys = [key for key in ("id", "machine_name", "tile_name", "record_type", "event_type", "scraped_date") if any(key in item for item in value)]
            rows = [[str(item.get(key, "")) for key in keys] for item in value]
            widths = [max(len(key), *(len(row[i]) for row in rows)) for i, key in enumerate(keys)]
            click.echo("  ".join(key.ljust(widths[i]) for i, key in enumerate(keys)))
            click.echo("  ".join("-" * width for width in widths))
            for row in rows:
                click.echo("  ".join(cell.ljust(widths[i]) for i, cell in enumerate(row)))
            return
    if isinstance(value, dict):
        for key, item in value.items():
            if isinstance(item, (dict, list)):
                click.echo(f"{key}:")
                click.echo(json.dumps(item, indent=2, ensure_ascii=False, default=str))
            else:
                click.echo(f"{key}: {item}")
        return
    click.echo(value)


def client_for(ctx: click.Context, *, auth: bool = False) -> ApiClient:
    context: Context = ctx.find_root().obj
    if auth and not context.config.token:
        raise click.ClickException("Authentication required; run `hbetl login` first.")
    return ApiClient(context.config.api_url, context.config.token)


def handle_error(exc: ApiError) -> None:
    prefix = f"HTTP {exc.status_code}: " if exc.status_code else ""
    raise click.ClickException(prefix + exc.message) from exc


@click.group(context_settings={"help_option_names": ["-h", "--help"]})
@click.option("--api-url", envvar="HBETL_API_URL", help="API base URL (default: http://localhost:5002).")
@click.option("--token", envvar="HBETL_TOKEN", help="Bearer token; overrides the saved token.")
@click.option("--config", "config_path", type=click.Path(path_type=None), help="Configuration file path.")
@click.option("--json", "as_json", is_flag=True, help="Print machine-readable JSON output.")
@click.version_option(__version__, prog_name="hbetl")
@click.pass_context
def main(ctx: click.Context, api_url: str | None, token: str | None, config_path: str | None, as_json: bool) -> None:
    """Command-line client for the Humble Book ETL API."""
    path = Path(os.path.expanduser(config_path)) if config_path else None
    try:
        config = load_config(path)
    except ValueError as exc:
        raise click.ClickException(str(exc)) from exc
    if api_url:
        config.api_url = api_url.rstrip("/")
    if token:
        config.token = token
    ctx.obj = Context(config, path or default_config_path())
    ctx.meta["as_json"] = as_json


@main.command()
@click.pass_context
def about(ctx: click.Context) -> None:
    """Show what this CLI does and where it stores credentials."""
    context: Context = ctx.find_root().obj
    click.echo("hbetl — command-line client for the Humble Book ETL API")
    click.echo("Supports authentication, bundle queries, inactive-history pagination, raw data, and ETL runs.")
    click.echo(f"Version: {__version__}")
    click.echo(f"API: {context.config.api_url}")
    click.echo(f"Config: {context.config_path}")


@main.command()
def version() -> None:
    """Show the CLI version."""
    click.echo(__version__)


@main.command()
@click.option("--username", prompt=True, help="API username.")
@click.option("--password", prompt=True, hide_input=True, confirmation_prompt=False, envvar="HBETL_PASSWORD")
@click.pass_context
def login(ctx: click.Context, username: str, password: str) -> None:
    """Authenticate and save the API token locally."""
    context: Context = ctx.find_root().obj
    try:
        with ApiClient(context.config.api_url) as api:
            result = api.login(username, password)
    except ApiError as exc:
        handle_error(exc)
    context.config.token = result["access_token"]
    try:
        save_config(context.config, context.config_path)
    except OSError as exc:
        raise click.ClickException(f"Cannot save config file {context.config_path}: {exc}") from exc
    if ctx.find_root().meta["as_json"]:
        dump_json({"user": result.get("user"), "config": str(context.config_path)})
    else:
        click.echo(f"Logged in as {result.get('user', {}).get('username', username)}.")
        click.echo(f"Token saved to {context.config_path}.")


@main.command()
@click.pass_context
def logout(ctx: click.Context) -> None:
    """Remove the saved API token."""
    context: Context = ctx.find_root().obj
    context.config.token = None
    try:
        save_config(context.config, context.config_path)
    except OSError as exc:
        raise click.ClickException(f"Cannot save config file {context.config_path}: {exc}") from exc
    if ctx.find_root().meta["as_json"]:
        dump_json({"logged_out": True, "config": str(context.config_path)})
    else:
        click.echo("Logged out.")


@main.command()
@click.pass_context
def health(ctx: click.Context) -> None:
    """Check API availability."""
    try:
        with client_for(ctx) as api:
            result = api.health()
    except ApiError as exc:
        handle_error(exc)
    render(result, as_json=ctx.find_root().meta["as_json"])


@main.command()
@click.pass_context
def me(ctx: click.Context) -> None:
    """Show the authenticated user."""
    try:
        with client_for(ctx, auth=True) as api:
            result = api.me()
    except ApiError as exc:
        handle_error(exc)
    render(result, as_json=ctx.find_root().meta["as_json"])


@main.group()
def bundles() -> None:
    """List and inspect bundles."""


@bundles.command("list")
@click.option("--inactive", is_flag=True, help="Include retained inactive bundles.")
@click.option("--all", "all_items", is_flag=True, help="Follow inactive cursors until all available records are returned.")
@click.option("--page-size", type=click.IntRange(1, 1000), default=100, show_default=True)
@click.option("--max-items", type=click.IntRange(1), default=10_000, show_default=True, help="Maximum records when using --all.")
@click.option("--limit", type=click.IntRange(1, 1000), default=None, help="Maximum records for one request.")
@click.option("--offset", type=click.IntRange(0), default=0, show_default=True)
@click.option("--snapshot-at", help="UTC snapshot boundary for cursor pagination.")
@click.option("--before-end-date", help="UTC end-date cursor for inactive pagination.")
@click.option("--before-id", help="Bundle ID cursor for inactive pagination.")
@click.pass_context
def bundles_list(ctx: click.Context, inactive: bool, all_items: bool, page_size: int, max_items: int,
                limit: int | None, offset: int, snapshot_at: str | None,
                before_end_date: str | None, before_id: str | None) -> None:
    """List active bundles, or page through inactive history."""
    if all_items and (limit is not None or offset or snapshot_at or before_end_date or before_id):
        raise click.UsageError("--all cannot be combined with --limit, --offset, or cursor options")
    if (snapshot_at or before_end_date or before_id) and not inactive:
        raise click.UsageError("cursor options require --inactive")
    try:
        with client_for(ctx) as api:
            if all_items:
                if not inactive:
                    raise click.UsageError("--all requires --inactive")
                result = list(api.iter_inactive_bundles(page_size=page_size, max_items=max_items))
            else:
                result, snapshot = api.list_bundles(
                    include_inactive=inactive,
                    limit=limit,
                    offset=offset,
                    snapshot_at=snapshot_at,
                    before_end_date=before_end_date,
                    before_id=before_id,
                )
                if inactive and snapshot and not ctx.find_root().meta["as_json"]:
                    click.echo(f"Snapshot: {snapshot}")
    except ApiError as exc:
        handle_error(exc)
    render(result, as_json=ctx.find_root().meta["as_json"])


@bundles.command("get")
@click.argument("identifier")
@click.option("--machine-name", is_flag=True, help="Treat IDENTIFIER as machine_name instead of UUID.")
@click.pass_context
def bundles_get(ctx: click.Context, identifier: str, machine_name: bool) -> None:
    """Get a bundle by UUID or machine name."""
    try:
        with client_for(ctx) as api:
            result = api.get_bundle(identifier, machine_name=machine_name)
    except ApiError as exc:
        handle_error(exc)
    render(result, as_json=ctx.find_root().meta["as_json"])


@bundles.command("featured")
@click.pass_context
def bundles_featured(ctx: click.Context) -> None:
    """Show the featured bundle."""
    try:
        with client_for(ctx) as api:
            result = api.featured_bundle()
    except ApiError as exc:
        handle_error(exc)
    render(result, as_json=ctx.find_root().meta["as_json"])


@bundles.command("raw-html")
@click.argument("bundle_id")
@click.option("--output", "output_path", type=click.Path(dir_okay=False, writable=True, path_type=None), help="Write HTML to a file instead of stdout.")
@click.pass_context
def bundles_raw_html(ctx: click.Context, bundle_id: str, output_path: str | None) -> None:
    """Fetch retained raw HTML for an authenticated bundle."""
    try:
        with client_for(ctx, auth=True) as api:
            result = api.raw_html(bundle_id)
    except ApiError as exc:
        handle_error(exc)
    raw_html = result.get("raw_html") or ""
    if output_path:
        with open(output_path, "w", encoding="utf-8") as output:
            output.write(raw_html)
        click.echo(output_path)
    elif ctx.find_root().meta["as_json"]:
        dump_json(result)
    else:
        click.echo(raw_html, nl=False)


@main.group(name="raw-data")
def raw_data() -> None:
    """Inspect stored landing-page snapshots."""


@raw_data.command("list")
@click.pass_context
def raw_data_list(ctx: click.Context) -> None:
    """List all stored landing-page snapshots."""
    try:
        with client_for(ctx) as api:
            result = api.raw_data()
    except ApiError as exc:
        handle_error(exc)
    render(result, as_json=ctx.find_root().meta["as_json"])


@raw_data.command("latest")
@click.pass_context
def raw_data_latest(ctx: click.Context) -> None:
    """Show the latest landing-page snapshot."""
    try:
        with client_for(ctx) as api:
            result = api.raw_data(latest=True)
    except ApiError as exc:
        handle_error(exc)
    render(result, as_json=ctx.find_root().meta["as_json"])


@raw_data.command("get")
@click.argument("raw_data_id")
@click.pass_context
def raw_data_get(ctx: click.Context, raw_data_id: str) -> None:
    """Show one landing-page snapshot by ID."""
    try:
        with client_for(ctx) as api:
            result = api.raw_data(raw_data_id)
    except ApiError as exc:
        handle_error(exc)
    render(result, as_json=ctx.find_root().meta["as_json"])


@main.group()
def etl() -> None:
    """Run protected ETL operations."""


@etl.command("run")
@click.pass_context
def etl_run(ctx: click.Context) -> None:
    """Run the protected ETL job."""
    try:
        with client_for(ctx, auth=True) as api:
            result = api.run_etl()
    except ApiError as exc:
        handle_error(exc)
    render(result, as_json=ctx.find_root().meta["as_json"])


@main.command(name="lifecycle-events")
@click.option("--machine-name")
@click.option("--event-type", type=click.Choice(["extended", "renewed", "shortened", "reactivated"]))
@click.option("--limit", type=click.IntRange(1, 1000), default=100, show_default=True)
@click.option("--offset", type=click.IntRange(0), default=0, show_default=True)
@click.pass_context
def lifecycle_events(ctx: click.Context, machine_name: str | None, event_type: str | None, limit: int, offset: int) -> None:
    """List bundle lifecycle events."""
    try:
        with client_for(ctx) as api:
            result = api.lifecycle_events(machine_name=machine_name, event_type=event_type, limit=limit, offset=offset)
    except ApiError as exc:
        handle_error(exc)
    render(result, as_json=ctx.find_root().meta["as_json"])
