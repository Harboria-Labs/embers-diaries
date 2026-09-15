"""Behavioral tests for Ember's supported configuration contract."""

from __future__ import annotations

import asyncio
import json
import os
from pathlib import Path
import subprocess
import sys

import pytest

from embers.config import ConfigError, load_config
from embers.mcp.server import EmberMCP


def _write_config(path: Path, text: str) -> Path:
    path.write_text(text, encoding="utf-8")
    return path


def test_defaults_are_current_runtime_defaults(tmp_path: Path, monkeypatch):
    monkeypatch.chdir(tmp_path)
    config = load_config(env={})

    assert config.storage.path == Path("ember_store")
    assert config.maintenance.interval_seconds == 0
    assert config.maintenance.namespaces == ("memories",)
    assert config.maintenance.enabled is False


def test_precedence_defaults_file_environment_then_explicit(tmp_path: Path):
    config_file = _write_config(tmp_path / "ember.toml", """
[storage]
path = "from-file"

[maintenance]
interval_seconds = 30
namespaces = ["file-a", "file-b"]
""")

    config = load_config(
        config_file,
        env={
            "EMBER_STORE": "from-environment",
            "EMBER_MAINTENANCE_INTERVAL_SECONDS": "45",
            "EMBER_MAINTENANCE_NAMESPACES": "env-a, env-b",
        },
        storage_path="from-explicit",
        maintenance_interval_seconds=60,
        maintenance_namespaces=["explicit-a", "explicit-b"],
    )

    assert config.storage.path == Path("from-explicit")
    assert config.maintenance.interval_seconds == 60
    assert config.maintenance.namespaces == ("explicit-a", "explicit-b")
    assert config.maintenance.enabled is True


def test_each_lower_precedence_layer_is_observable(tmp_path: Path):
    config_file = _write_config(tmp_path / "ember.toml", """
[storage]
path = "from-file"
[maintenance]
interval_seconds = 30
namespaces = ["file"]
""")

    from_file = load_config(config_file, env={})
    assert from_file.storage.path == Path("from-file")
    assert from_file.maintenance.namespaces == ("file",)

    from_environment = load_config(config_file, env={"EMBER_STORE": "from-env"})
    assert from_environment.storage.path == Path("from-env")
    assert from_environment.maintenance.interval_seconds == 30


def test_ember_config_selects_file_and_missing_explicit_file_fails(tmp_path: Path):
    config_file = _write_config(tmp_path / "selected.toml", """
[storage]
path = "selected-store"
""")

    assert load_config(env={"EMBER_CONFIG": str(config_file)}).storage.path == Path(
        "selected-store")
    with pytest.raises(ConfigError, match="configuration file not found"):
        load_config(tmp_path / "missing.toml", env={})


@pytest.mark.parametrize(
    ("text", "message"),
    [
        ("[storage]\npath = ''\n", "storage.path"),
        ("[maintenance]\ninterval_seconds = -1\n", "interval_seconds"),
        ("[maintenance]\nnamespaces = []\n", "namespaces"),
        ("[search]\nmax_results = 10\n", "unsupported"),
        ("[storage]\nformat = 'msgpack'\n", "unsupported"),
    ],
)
def test_invalid_or_unsupported_file_settings_fail(
    tmp_path: Path, text: str, message: str,
):
    path = _write_config(tmp_path / "invalid.toml", text)
    with pytest.raises(ConfigError, match=message):
        load_config(path, env={})


def test_environment_values_are_validated():
    with pytest.raises(ConfigError, match="integer"):
        load_config(env={"EMBER_MAINTENANCE_INTERVAL_SECONDS": "often"})
    with pytest.raises(ConfigError, match="at least one"):
        load_config(env={"EMBER_MAINTENANCE_NAMESPACES": " , "})


def test_mcp_uses_loader_for_file_environment_and_programmatic_override(
    tmp_path: Path,
):
    file_store = tmp_path / "file-store"
    env_store = tmp_path / "env-store"
    explicit_store = tmp_path / "explicit-store"
    config_file = _write_config(tmp_path / "ember.toml", f"""
[storage]
path = {str(file_store)!r}
""")

    configured = load_config(config_file, env={"EMBER_STORE": str(env_store)})
    from_environment = EmberMCP(config=configured)
    from_explicit = EmberMCP(config_path=str(config_file), store_path=str(explicit_store))

    assert from_environment.db._path == env_store
    assert from_explicit.db._path == explicit_store
    with pytest.raises(ValueError, match="cannot be combined"):
        EmberMCP(config=configured, store_path=str(explicit_store))


def test_mcp_cli_store_override_has_highest_precedence(tmp_path: Path):
    file_store = tmp_path / "file-store"
    env_store = tmp_path / "env-store"
    cli_store = tmp_path / "cli-store"
    config_file = _write_config(tmp_path / "ember.toml", f"""
[storage]
path = {str(file_store)!r}
""")
    environment = dict(os.environ)
    environment["PYTHONDONTWRITEBYTECODE"] = "1"
    environment["EMBER_STORE"] = str(env_store)
    request = json.dumps({
        "jsonrpc": "2.0", "id": 1, "method": "initialize",
    }) + "\n"

    process = subprocess.run(
        [
            sys.executable, "-m", "embers.mcp", "--config", str(config_file),
            "--store", str(cli_store),
        ],
        input=request,
        capture_output=True,
        text=True,
        encoding="utf-8",
        env=environment,
        timeout=60,
        check=False,
    )

    assert process.returncode == 0, process.stderr
    assert json.loads(process.stdout)["result"]["serverInfo"]["name"] == (
        "ember-diaries")
    assert cli_store.is_dir()
    assert not env_store.exists()
    assert not file_store.exists()


def test_api_storage_and_scheduler_consume_one_loaded_config(
    tmp_path: Path, monkeypatch,
):
    import embers.api as api

    store = tmp_path / "api-store"
    config_file = _write_config(tmp_path / "ember.toml", f"""
[storage]
path = {str(store)!r}
[maintenance]
interval_seconds = 12
namespaces = ["one", "two"]
""")
    monkeypatch.setenv("EMBER_CONFIG", str(config_file))
    monkeypatch.delenv("EMBER_STORE", raising=False)
    monkeypatch.delenv("EMBER_MAINTENANCE_INTERVAL_SECONDS", raising=False)
    monkeypatch.delenv("EMBER_MAINTENANCE_NAMESPACES", raising=False)
    monkeypatch.setattr(api, "_config", None)
    monkeypatch.setattr(api, "_db", None)
    monkeypatch.setattr(api, "_protocol", None)

    db = api._get_db()
    assert db._path == store

    startup = next(
        handler for handler in api.app.router.on_startup
        if handler.__name__ == "_start_maintenance"
    )
    captured = {}

    async def fake_loop(protocol, namespaces, interval_seconds):
        captured.update(
            namespaces=namespaces, interval_seconds=interval_seconds)

    import embers.maintenance as maintenance
    monkeypatch.setattr(maintenance, "maintenance_loop", fake_loop)

    async def run_startup():
        await startup()
        await asyncio.sleep(0)

    asyncio.run(run_startup())
    assert captured == {"namespaces": ["one", "two"], "interval_seconds": 12}
