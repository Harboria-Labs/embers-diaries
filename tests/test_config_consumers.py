"""Behavioral proof that supported settings are connected to runtime behavior."""

from __future__ import annotations

import asyncio
from datetime import datetime, timedelta, timezone
import logging
import os
from pathlib import Path
import subprocess
import sys
from types import SimpleNamespace

import pytest
from starlette.requests import Request

from embers import Evidence, MemoryProposal, SourceType
from embers.config import (
    ApiConfig,
    ConfigError,
    EmberConfig,
    LobbyConfig,
    LoggingConfig,
    SearchConfig,
    load_config,
)
from embers.core.errors import StorageLimitError
from embers.lobby.store import LobbyError, LobbyStore
from embers.logging_config import configure_logging
from embers.mcp.server import EmberMCP


def test_full_toml_environment_and_override_contract(tmp_path: Path):
    path = tmp_path / "ember.toml"
    path.write_text("""
[storage]
path = "configured-store"
max_store_bytes = 10000
max_record_bytes = 2000
[api]
host = "127.0.0.1"
port = 9300
cors_origins = ["https://one.example"]
[lobby]
max_agents = 4
message_ttl_seconds = 30
presence_timeout_seconds = 45
heartbeat_interval_seconds = 15
max_message_bytes = 500
[search]
semantic_enabled = false
max_results = 7
default_threshold = 0.4
max_context_tokens = 900
[evidence]
require_evidence = true
minimum_items = 2
min_confidence = 0.6
verified_confidence = 0.9
[retention]
episodic_memory = 0.12
[logging]
level = "INFO"
file = "ember.log"
[native]
transport = "embedded"
endpoint = "local://configured"
""", encoding="utf-8")

    config = load_config(
        path,
        env={"EMBER_LOBBY_MAX_AGENTS": "5"},
        overrides={"api.port": 9400},
    )

    assert config.storage.max_record_bytes == 2000
    assert config.api.port == 9400
    assert config.api.cors_origins == ("https://one.example",)
    assert config.lobby.max_agents == 5
    assert config.search.semantic_enabled is False
    assert config.evidence.minimum_items == 2
    assert config.retention.episodic_memory == 0.12
    assert config.logging.level == "INFO"
    assert config.native.endpoint == "local://configured"


@pytest.mark.parametrize("overrides, message", [
    ({"evidence.min_confidence": 0.9,
      "evidence.verified_confidence": 0.8}, "verified_confidence"),
    ({"lobby.heartbeat_interval_seconds": 60,
      "lobby.presence_timeout_seconds": 60}, "heartbeat_interval"),
    ({"retention.raw_memory": "nan"}, "retention.raw_memory"),
])
def test_cross_field_and_finite_number_validation(overrides, message):
    with pytest.raises(ConfigError, match=message):
        load_config(env={}, overrides=overrides)


def test_native_process_mode_is_reserved_but_never_faked(tmp_path: Path):
    config = load_config(env={}, overrides={
        "storage.path": tmp_path / "store",
        "native.transport": "process",
    })
    assert config.native.uses_ipc
    with pytest.raises(ConfigError, match="not available yet"):
        EmberMCP(config=config)
    assert not (tmp_path / "store").exists()


def test_storage_record_and_store_quotas_reject_before_wal(tmp_path: Path):
    record_limited = load_config(env={}, overrides={
        "storage.path": tmp_path / "record-limited",
        "storage.max_record_bytes": 1,
    })
    first = EmberMCP(config=record_limited)
    with pytest.raises(StorageLimitError, match="max_record_bytes"):
        first.protocol.remember("too large")
    assert first.db._store.record_count() == 0
    assert first.db._store.wal.size_bytes() == 0

    store_limited = load_config(env={}, overrides={
        "storage.path": tmp_path / "store-limited",
        "storage.max_store_bytes": 1,
    })
    second = EmberMCP(config=store_limited)
    with pytest.raises(StorageLimitError, match="max_store_bytes"):
        second.protocol.remember("too large")
    assert second.db._store.record_count() == 0
    assert second.db._store.wal.size_bytes() == 0


def test_retention_config_sets_new_record_rate_without_fixing_future_policy(
    tmp_path: Path,
):
    config = load_config(env={}, overrides={
        "storage.path": tmp_path / "store",
        "retention.episodic_memory": 0.123,
    })
    mcp = EmberMCP(config=config)
    record_id = mcp.protocol.remember("event", memory_type="episodic")
    assert mcp.db.get(record_id).decay_rate == 0.123


def test_search_config_disables_semantic_lookup_and_caps_results(tmp_path: Path):
    config = load_config(env={}, overrides={
        "storage.path": tmp_path / "store",
        "search.semantic_enabled": False,
        "search.max_results": 1,
        "search.max_context_tokens": 321,
    })
    mcp = EmberMCP(config=config)
    mcp.protocol.remember("needle alpha")
    mcp.protocol.remember("needle beta")

    def semantic_lookup_must_not_run(*args, **kwargs):
        raise AssertionError("semantic lookup ran while disabled")

    mcp.db.similar = semantic_lookup_must_not_run
    records = mcp.protocol.recall("needle", top_k=20, format="raw")
    assert len(records) == 1
    assert mcp.protocol.context_builder.max_tokens == 321


def test_evidence_config_controls_automatic_promotion(tmp_path: Path):
    config = load_config(env={}, overrides={
        "storage.path": tmp_path / "store",
        "evidence.minimum_items": 2,
    })
    mcp = EmberMCP(config=config)
    evidence = Evidence(
        source="tool://run/1",
        source_type=SourceType.EXPERIMENTALLY_VERIFIED,
        description="one observation",
        agent_id="agent-a",
    )
    proposal = MemoryProposal(
        namespace="memories",
        discovery={"claim": "x"},
        reason="test",
        evidence=[evidence],
        confidence=0.95,
        agent_id="agent-a",
    )
    result = mcp.db.submit(mcp.db.propose(proposal))
    assert not result.promoted
    assert any("need 2" in reason for reason in result.decision.reasons)


def test_lobby_limits_ttl_presence_and_disabled_mode():
    limited = LobbyStore(LobbyConfig(
        max_agents=1,
        max_message_bytes=4,
        message_ttl_seconds=1,
    ))
    limited.open(
        session_id="s1", agent_id="a1", task="t", namespace="n", room="task")
    with pytest.raises(LobbyError, match="max_agents"):
        limited.open(
            session_id="s2", agent_id="a2", task="t", namespace="n", room="task")
    with pytest.raises(LobbyError, match="max_message_bytes"):
        limited.publish(
            session_id="s1", agent_id="a1", room="task",
            post_type="question", body="12345", approach=None)
    post = limited.publish(
        session_id="s1", agent_id="a1", room="task",
        post_type="question", body="1234", approach=None)
    board = limited.boards[post["board_id"]]
    board["posts"][0]["created_at"] = (
        datetime.now(timezone.utc) - timedelta(seconds=2)).isoformat()
    assert limited.snapshot(session_id="s1")["posts"] == []
    assert limited.heartbeat(session_id="s1", agent_id="a1")["agent_id"] == "a1"
    assert limited.snapshot(session_id="s1")["presence"][0]["status"] == "online"

    disabled = LobbyStore(LobbyConfig(enabled=False))
    assert disabled.summary_for_session("s") is None
    with pytest.raises(LobbyError, match="disabled"):
        disabled.open(
            session_id="s", agent_id="a", task="t", namespace="n", room="task")


def test_logging_configuration_writes_ember_logs_only(tmp_path: Path):
    log_file = tmp_path / "logs" / "ember.log"
    configure_logging(LoggingConfig(
        level="INFO", file=log_file, max_bytes=1024, backup_count=1))
    logging.getLogger("embers.config-test").info("configured message")
    for handler in logging.getLogger("embers").handlers:
        handler.flush()
    assert "configured message" in log_file.read_text(encoding="utf-8")


def test_api_surface_enable_flags_are_enforced(monkeypatch):
    import embers.api as api

    async def downstream(_request):
        raise AssertionError("disabled request reached route")

    def request(path: str) -> Request:
        return Request({
            "type": "http", "method": "GET", "path": path,
            "headers": [], "query_string": b"", "scheme": "http",
            "server": ("test", 80), "client": ("test", 1),
            "root_path": "", "http_version": "1.1",
        })

    monkeypatch.setattr(api, "_config", EmberConfig(
        api=ApiConfig(rest_enabled=False, mcp_enabled=False)))
    rest = asyncio.run(api.require_enabled_surface(request("/v1/memory"), downstream))
    mcp = asyncio.run(api.require_enabled_surface(request("/mcp"), downstream))
    assert rest.status_code == 503
    assert mcp.status_code == 503


def test_api_cors_allowlist_is_loaded_at_process_start(tmp_path: Path):
    config_file = tmp_path / "cors.toml"
    config_file.write_text(
        '[api]\ncors_origins = ["https://trusted.example"]\n',
        encoding="utf-8",
    )
    environment = dict(os.environ)
    environment["EMBER_CONFIG"] = str(config_file)
    environment["PYTHONDONTWRITEBYTECODE"] = "1"
    process = subprocess.run(
        [
            sys.executable,
            "-c",
            "import embers.api as a; "
            "print(next(m.kwargs['allow_origins'] for m in "
            "a.app.user_middleware if m.cls.__name__ == 'CORSMiddleware'))",
        ],
        capture_output=True,
        text=True,
        encoding="utf-8",
        env=environment,
        timeout=60,
        check=False,
    )
    assert process.returncode == 0, process.stderr
    assert process.stdout.strip() == "['https://trusted.example']"


def test_api_cli_consumes_configured_address_and_explicit_overrides(
    tmp_path: Path, monkeypatch,
):
    config_file = tmp_path / "api.toml"
    config_file.write_text(
        '[api]\nhost = "127.0.0.1"\nport = 9300\n', encoding="utf-8")
    captured = {}

    def run(app, *, host, port):
        captured.update(app=app, host=host, port=port)

    monkeypatch.setitem(sys.modules, "uvicorn", SimpleNamespace(run=run))
    from embers import api_cli
    api_cli.main([
        "--config", str(config_file), "--host", "localhost", "--port", "9400",
    ])
    assert captured["host"] == "localhost"
    assert captured["port"] == 9400
