from pathlib import Path

from click.testing import CliRunner

from hbetl_cli import __version__
from hbetl_cli.cli import main
from hbetl_cli.config import Config, load_config, save_config


def test_about_and_version_are_available():
    runner = CliRunner()

    about = runner.invoke(main, ["about"])
    version = runner.invoke(main, ["version"])
    flag = runner.invoke(main, ["--version"])

    assert about.exit_code == 0, about.output
    assert "command-line client" in about.output
    assert f"Version: {__version__}" in about.output
    assert version.output.strip() == __version__
    assert flag.output.strip() == f"hbetl, version {__version__}"


def test_package_help_lists_api_capabilities():
    result = CliRunner().invoke(main, ["--help"])

    assert result.exit_code == 0
    for command in ("about", "version", "auth", "bundles", "raw-data", "etl"):
        assert command in result.output

    auth = CliRunner().invoke(main, ["auth", "--help"])
    assert auth.exit_code == 0
    assert "login" in auth.output
    assert "logout" in auth.output
    assert "me" in auth.output


def test_config_is_saved_with_restricted_permissions(tmp_path: Path):
    config_path = tmp_path / "config.json"

    save_config(Config(api_url="https://api.example.test", token="secret"), config_path)
    loaded = load_config(config_path)

    assert loaded.api_url == "https://api.example.test"
    assert loaded.token == "secret"
    assert config_path.stat().st_mode & 0o777 == 0o600
