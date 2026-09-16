"""Typed, validated configuration for behavior Ember actually consumes.

Precedence is defaults, TOML, environment, then explicit overrides. Secrets
are deliberately excluded: agent tokens and future IPC credentials remain
runtime inputs supplied by an environment or secret manager.
"""

from __future__ import annotations

from dataclasses import dataclass, field
import math
import os
from pathlib import Path
from typing import Any, Mapping, Sequence

try:  # Python 3.11+
    import tomllib
except ModuleNotFoundError:  # pragma: no cover - Python 3.10
    import tomli as tomllib


DEFAULT_CONFIG_PATH = Path("config.toml")
DEFAULT_STORAGE_PATH = Path("./ember_store")

_SUPPORTED_KEYS = {
    "storage": {"path", "max_store_bytes", "max_record_bytes"},
    "maintenance": {"interval_seconds", "namespaces"},
    "api": {"rest_enabled", "mcp_enabled", "host", "port", "cors_origins"},
    "lobby": {
        "enabled", "max_agents", "message_ttl_seconds",
        "presence_timeout_seconds", "heartbeat_interval_seconds",
        "max_message_bytes",
    },
    "search": {
        "semantic_enabled", "max_results", "default_threshold",
        "max_context_tokens",
    },
    "evidence": {
        "require_evidence", "minimum_items", "min_confidence",
        "verified_confidence",
    },
    "retention": {
        "raw_memory", "skill_memory", "failure_memory", "episodic_memory",
        "connective_memory", "reflective_memory", "unscoped_memory",
    },
    "logging": {"level", "file", "max_bytes", "backup_count"},
    "native": {
        "transport", "endpoint", "connect_timeout_seconds",
        "request_timeout_seconds", "startup_timeout_seconds",
        "max_frame_bytes",
    },
}

_ENV_KEYS = {
    "EMBER_STORE": "storage.path",
    "EMBER_STORAGE_MAX_STORE_BYTES": "storage.max_store_bytes",
    "EMBER_STORAGE_MAX_RECORD_BYTES": "storage.max_record_bytes",
    "EMBER_MAINTENANCE_INTERVAL_SECONDS": "maintenance.interval_seconds",
    "EMBER_MAINTENANCE_NAMESPACES": "maintenance.namespaces",
    "EMBER_API_REST_ENABLED": "api.rest_enabled",
    "EMBER_API_MCP_ENABLED": "api.mcp_enabled",
    "EMBER_API_HOST": "api.host",
    "EMBER_API_PORT": "api.port",
    "EMBER_CORS_ORIGINS": "api.cors_origins",
    "EMBER_LOBBY_ENABLED": "lobby.enabled",
    "EMBER_LOBBY_MAX_AGENTS": "lobby.max_agents",
    "EMBER_LOBBY_MESSAGE_TTL_SECONDS": "lobby.message_ttl_seconds",
    "EMBER_LOBBY_PRESENCE_TIMEOUT_SECONDS": "lobby.presence_timeout_seconds",
    "EMBER_LOBBY_HEARTBEAT_INTERVAL_SECONDS": "lobby.heartbeat_interval_seconds",
    "EMBER_LOBBY_MAX_MESSAGE_BYTES": "lobby.max_message_bytes",
    "EMBER_SEARCH_SEMANTIC_ENABLED": "search.semantic_enabled",
    "EMBER_SEARCH_MAX_RESULTS": "search.max_results",
    "EMBER_SEARCH_DEFAULT_THRESHOLD": "search.default_threshold",
    "EMBER_SEARCH_MAX_CONTEXT_TOKENS": "search.max_context_tokens",
    "EMBER_EVIDENCE_REQUIRE_EVIDENCE": "evidence.require_evidence",
    "EMBER_EVIDENCE_MINIMUM_ITEMS": "evidence.minimum_items",
    "EMBER_EVIDENCE_MIN_CONFIDENCE": "evidence.min_confidence",
    "EMBER_EVIDENCE_VERIFIED_CONFIDENCE": "evidence.verified_confidence",
    "EMBER_RETENTION_RAW_MEMORY": "retention.raw_memory",
    "EMBER_RETENTION_SKILL_MEMORY": "retention.skill_memory",
    "EMBER_RETENTION_FAILURE_MEMORY": "retention.failure_memory",
    "EMBER_RETENTION_EPISODIC_MEMORY": "retention.episodic_memory",
    "EMBER_RETENTION_CONNECTIVE_MEMORY": "retention.connective_memory",
    "EMBER_RETENTION_REFLECTIVE_MEMORY": "retention.reflective_memory",
    "EMBER_RETENTION_UNSCOPED_MEMORY": "retention.unscoped_memory",
    "EMBER_LOG_LEVEL": "logging.level",
    "EMBER_LOG_FILE": "logging.file",
    "EMBER_LOG_MAX_BYTES": "logging.max_bytes",
    "EMBER_LOG_BACKUP_COUNT": "logging.backup_count",
    "EMBER_NATIVE_TRANSPORT": "native.transport",
    "EMBER_NATIVE_ENDPOINT": "native.endpoint",
    "EMBER_NATIVE_CONNECT_TIMEOUT_SECONDS": "native.connect_timeout_seconds",
    "EMBER_NATIVE_REQUEST_TIMEOUT_SECONDS": "native.request_timeout_seconds",
    "EMBER_NATIVE_STARTUP_TIMEOUT_SECONDS": "native.startup_timeout_seconds",
    "EMBER_NATIVE_MAX_FRAME_BYTES": "native.max_frame_bytes",
}


class ConfigError(ValueError):
    """A configuration source contains an unsupported or invalid value."""


@dataclass(frozen=True)
class StorageConfig:
    path: Path = DEFAULT_STORAGE_PATH
    max_store_bytes: int = 0       # 0 = unlimited
    max_record_bytes: int = 0      # 0 = unlimited


@dataclass(frozen=True)
class MaintenanceConfig:
    interval_seconds: int = 0
    namespaces: tuple[str, ...] = ("memories",)

    @property
    def enabled(self) -> bool:
        return self.interval_seconds > 0


@dataclass(frozen=True)
class ApiConfig:
    rest_enabled: bool = True
    mcp_enabled: bool = True
    host: str = "127.0.0.1"
    port: int = 9200
    cors_origins: tuple[str, ...] = ()


@dataclass(frozen=True)
class LobbyConfig:
    enabled: bool = True
    max_agents: int = 0            # 0 = unlimited
    message_ttl_seconds: int = 0   # 0 = no expiry
    presence_timeout_seconds: int = 60
    heartbeat_interval_seconds: int = 20
    max_message_bytes: int = 0     # 0 = unlimited


@dataclass(frozen=True)
class SearchConfig:
    semantic_enabled: bool = True
    max_results: int = 100
    default_threshold: float = 0.0
    max_context_tokens: int = 4096


@dataclass(frozen=True)
class EvidenceConfig:
    require_evidence: bool = True
    minimum_items: int = 1
    min_confidence: float = 0.7
    verified_confidence: float = 0.85


@dataclass(frozen=True)
class RetentionConfig:
    raw_memory: float = 0.01
    skill_memory: float = 0.005
    failure_memory: float = 0.0
    episodic_memory: float = 0.02
    connective_memory: float = 0.005
    reflective_memory: float = 0.01
    unscoped_memory: float = 0.01


@dataclass(frozen=True)
class LoggingConfig:
    level: str = "WARNING"
    file: Path | None = None
    max_bytes: int = 10 * 1024 * 1024
    backup_count: int = 5


@dataclass(frozen=True)
class NativeConfig:
    transport: str = "embedded"
    endpoint: str = "local://ember-core"
    connect_timeout_seconds: float = 5.0
    request_timeout_seconds: float = 30.0
    startup_timeout_seconds: float = 30.0
    max_frame_bytes: int = 16 * 1024 * 1024

    @property
    def uses_ipc(self) -> bool:
        return self.transport == "process"

    def require_available(self) -> None:
        if self.uses_ipc:
            raise ConfigError(
                "native.transport='process' is reserved for the native IPC "
                "milestone and is not available yet; use 'embedded'")


@dataclass(frozen=True)
class EmberConfig:
    storage: StorageConfig = field(default_factory=StorageConfig)
    maintenance: MaintenanceConfig = field(default_factory=MaintenanceConfig)
    api: ApiConfig = field(default_factory=ApiConfig)
    lobby: LobbyConfig = field(default_factory=LobbyConfig)
    search: SearchConfig = field(default_factory=SearchConfig)
    evidence: EvidenceConfig = field(default_factory=EvidenceConfig)
    retention: RetentionConfig = field(default_factory=RetentionConfig)
    logging: LoggingConfig = field(default_factory=LoggingConfig)
    native: NativeConfig = field(default_factory=NativeConfig)

    def require_runtime_supported(self) -> None:
        self.native.require_available()


def _read_toml(path: Path, *, required: bool) -> dict[str, Any]:
    try:
        with path.open("rb") as stream:
            loaded = tomllib.load(stream)
    except FileNotFoundError:
        if required:
            raise ConfigError(f"configuration file not found: {path}") from None
        return {}
    except tomllib.TOMLDecodeError as exc:
        raise ConfigError(f"invalid TOML in {path}: {exc}") from exc
    if not isinstance(loaded, dict):
        raise ConfigError(f"configuration root must be a table: {path}")
    return loaded


def _validate_keys(values: Mapping[str, Any], source: str) -> None:
    unknown_sections = set(values) - set(_SUPPORTED_KEYS)
    if unknown_sections:
        names = ", ".join(sorted(unknown_sections))
        raise ConfigError(f"unsupported {source} section(s): {names}")
    for section, raw in values.items():
        if not isinstance(raw, Mapping):
            raise ConfigError(f"{source} [{section}] must be a table")
        unknown = set(raw) - _SUPPORTED_KEYS[section]
        if unknown:
            names = ", ".join(f"{section}.{key}" for key in sorted(unknown))
            raise ConfigError(f"unsupported {source} setting(s): {names}")


def _set(values: dict[str, Any], dotted: str, value: Any) -> None:
    section, key = dotted.split(".", 1)
    if section not in _SUPPORTED_KEYS or key not in _SUPPORTED_KEYS[section]:
        raise ConfigError(f"unsupported explicit setting: {dotted}")
    values.setdefault(section, {})[key] = value


def _path(value: Any, name: str) -> Path:
    if not isinstance(value, (str, os.PathLike)) or isinstance(value, bytes):
        raise ConfigError(f"{name} must be a non-empty path")
    text = os.fspath(value).strip()
    if not text:
        raise ConfigError(f"{name} must be a non-empty path")
    return Path(text).expanduser()


def _optional_path(value: Any, name: str) -> Path | None:
    if value in (None, ""):
        return None
    return _path(value, name)


def _integer(value: Any, name: str, *, minimum: int = 0,
             maximum: int | None = None) -> int:
    if isinstance(value, bool):
        raise ConfigError(f"{name} must be an integer >= {minimum}")
    try:
        parsed = int(value)
    except (TypeError, ValueError):
        raise ConfigError(f"{name} must be an integer >= {minimum}") from None
    if isinstance(value, float) and not value.is_integer() or parsed < minimum:
        raise ConfigError(f"{name} must be an integer >= {minimum}")
    if maximum is not None and parsed > maximum:
        raise ConfigError(f"{name} must be <= {maximum}")
    return parsed


def _number(value: Any, name: str, *, minimum: float = 0.0,
            maximum: float | None = None) -> float:
    if isinstance(value, bool):
        raise ConfigError(f"{name} must be a number >= {minimum}")
    try:
        parsed = float(value)
    except (TypeError, ValueError):
        raise ConfigError(f"{name} must be a number >= {minimum}") from None
    if (not math.isfinite(parsed) or parsed < minimum
            or maximum is not None and parsed > maximum):
        suffix = f" and <= {maximum}" if maximum is not None else ""
        raise ConfigError(f"{name} must be >= {minimum}{suffix}")
    return parsed


def _boolean(value: Any, name: str) -> bool:
    if isinstance(value, bool):
        return value
    if isinstance(value, str):
        normalized = value.strip().lower()
        if normalized in {"1", "true", "yes", "on"}:
            return True
        if normalized in {"0", "false", "no", "off"}:
            return False
    raise ConfigError(f"{name} must be true or false")


def _text(value: Any, name: str) -> str:
    if not isinstance(value, str) or not value.strip():
        raise ConfigError(f"{name} must be a non-empty string")
    return value.strip()


def _strings(value: Any, name: str, *, allow_empty: bool = False) -> tuple[str, ...]:
    if isinstance(value, str):
        items: Sequence[Any] = value.split(",")
    elif isinstance(value, Sequence) and not isinstance(value, (bytes, bytearray)):
        items = value
    else:
        raise ConfigError(f"{name} must be an array or comma-separated string")
    result: list[str] = []
    for item in items:
        if not isinstance(item, str):
            raise ConfigError(f"{name} entries must be strings")
        item = item.strip()
        if item and item not in result:
            result.append(item)
    if not result and not allow_empty:
        raise ConfigError(f"{name} must contain at least one value")
    return tuple(result)


def _section(values: Mapping[str, Any], name: str) -> Mapping[str, Any]:
    raw = values.get(name, {})
    return raw if isinstance(raw, Mapping) else {}


def load_config(
    config_path: str | os.PathLike[str] | None = None,
    *,
    env: Mapping[str, str] | None = None,
    storage_path: str | os.PathLike[str] | None = None,
    maintenance_interval_seconds: int | str | None = None,
    maintenance_namespaces: Sequence[str] | str | None = None,
    overrides: Mapping[str, Any] | None = None,
) -> EmberConfig:
    """Load configuration with defaults < TOML < environment < overrides."""

    environment = os.environ if env is None else env
    selected = config_path or environment.get("EMBER_CONFIG")
    explicit_file = selected is not None
    path = Path(selected) if explicit_file else DEFAULT_CONFIG_PATH
    values = _read_toml(path, required=explicit_file)
    _validate_keys(values, f"configuration file {path}")
    merged = {section: dict(raw) for section, raw in values.items()}

    for env_name, dotted in _ENV_KEYS.items():
        if env_name in environment:
            _set(merged, dotted, environment[env_name])
    if storage_path is not None:
        _set(merged, "storage.path", storage_path)
    if maintenance_interval_seconds is not None:
        _set(merged, "maintenance.interval_seconds", maintenance_interval_seconds)
    if maintenance_namespaces is not None:
        _set(merged, "maintenance.namespaces", maintenance_namespaces)
    for dotted, value in (overrides or {}).items():
        _set(merged, dotted, value)

    storage = _section(merged, "storage")
    maintenance = _section(merged, "maintenance")
    api = _section(merged, "api")
    lobby = _section(merged, "lobby")
    search = _section(merged, "search")
    evidence = _section(merged, "evidence")
    retention = _section(merged, "retention")
    logging_values = _section(merged, "logging")
    native = _section(merged, "native")

    config = EmberConfig(
        storage=StorageConfig(
            path=_path(storage.get("path", DEFAULT_STORAGE_PATH), "storage.path"),
            max_store_bytes=_integer(storage.get("max_store_bytes", 0), "storage.max_store_bytes"),
            max_record_bytes=_integer(storage.get("max_record_bytes", 0), "storage.max_record_bytes"),
        ),
        maintenance=MaintenanceConfig(
            interval_seconds=_integer(maintenance.get("interval_seconds", 0), "maintenance.interval_seconds"),
            namespaces=_strings(maintenance.get("namespaces", ("memories",)), "maintenance.namespaces"),
        ),
        api=ApiConfig(
            rest_enabled=_boolean(api.get("rest_enabled", True), "api.rest_enabled"),
            mcp_enabled=_boolean(api.get("mcp_enabled", True), "api.mcp_enabled"),
            host=_text(api.get("host", "127.0.0.1"), "api.host"),
            port=_integer(api.get("port", 9200), "api.port", minimum=1, maximum=65535),
            cors_origins=_strings(api.get("cors_origins", ()), "api.cors_origins", allow_empty=True),
        ),
        lobby=LobbyConfig(
            enabled=_boolean(lobby.get("enabled", True), "lobby.enabled"),
            max_agents=_integer(lobby.get("max_agents", 0), "lobby.max_agents"),
            message_ttl_seconds=_integer(lobby.get("message_ttl_seconds", 0), "lobby.message_ttl_seconds"),
            presence_timeout_seconds=_integer(lobby.get("presence_timeout_seconds", 60), "lobby.presence_timeout_seconds", minimum=1),
            heartbeat_interval_seconds=_integer(lobby.get("heartbeat_interval_seconds", 20), "lobby.heartbeat_interval_seconds", minimum=1),
            max_message_bytes=_integer(lobby.get("max_message_bytes", 0), "lobby.max_message_bytes"),
        ),
        search=SearchConfig(
            semantic_enabled=_boolean(search.get("semantic_enabled", True), "search.semantic_enabled"),
            max_results=_integer(search.get("max_results", 100), "search.max_results", minimum=1),
            default_threshold=_number(search.get("default_threshold", 0.0), "search.default_threshold", maximum=1.0),
            max_context_tokens=_integer(search.get("max_context_tokens", 4096), "search.max_context_tokens", minimum=1),
        ),
        evidence=EvidenceConfig(
            require_evidence=_boolean(evidence.get("require_evidence", True), "evidence.require_evidence"),
            minimum_items=_integer(evidence.get("minimum_items", 1), "evidence.minimum_items"),
            min_confidence=_number(evidence.get("min_confidence", 0.7), "evidence.min_confidence", maximum=1.0),
            verified_confidence=_number(evidence.get("verified_confidence", 0.85), "evidence.verified_confidence", maximum=1.0),
        ),
        retention=RetentionConfig(**{
            key: _number(
                retention.get(key, default), f"retention.{key}", maximum=1.0)
            for key, default in {
                "raw_memory": 0.01, "skill_memory": 0.005,
                "failure_memory": 0.0, "episodic_memory": 0.02,
                "connective_memory": 0.005, "reflective_memory": 0.01,
                "unscoped_memory": 0.01,
            }.items()
        }),
        logging=LoggingConfig(
            level=_text(logging_values.get("level", "WARNING"), "logging.level").upper(),
            file=_optional_path(logging_values.get("file"), "logging.file"),
            max_bytes=_integer(logging_values.get("max_bytes", 10 * 1024 * 1024), "logging.max_bytes", minimum=1),
            backup_count=_integer(logging_values.get("backup_count", 5), "logging.backup_count"),
        ),
        native=NativeConfig(
            transport=_text(native.get("transport", "embedded"), "native.transport").lower(),
            endpoint=_text(native.get("endpoint", "local://ember-core"), "native.endpoint"),
            connect_timeout_seconds=_number(native.get("connect_timeout_seconds", 5.0), "native.connect_timeout_seconds", minimum=0.001),
            request_timeout_seconds=_number(native.get("request_timeout_seconds", 30.0), "native.request_timeout_seconds", minimum=0.001),
            startup_timeout_seconds=_number(native.get("startup_timeout_seconds", 30.0), "native.startup_timeout_seconds", minimum=0.001),
            max_frame_bytes=_integer(native.get("max_frame_bytes", 16 * 1024 * 1024), "native.max_frame_bytes", minimum=1),
        ),
    )

    if config.evidence.verified_confidence < config.evidence.min_confidence:
        raise ConfigError(
            "evidence.verified_confidence must be >= evidence.min_confidence")
    if config.lobby.heartbeat_interval_seconds >= config.lobby.presence_timeout_seconds:
        raise ConfigError(
            "lobby.heartbeat_interval_seconds must be less than "
            "lobby.presence_timeout_seconds")
    if config.logging.level not in {"CRITICAL", "ERROR", "WARNING", "INFO", "DEBUG"}:
        raise ConfigError("logging.level must be CRITICAL, ERROR, WARNING, INFO, or DEBUG")
    if config.native.transport not in {"embedded", "process"}:
        raise ConfigError("native.transport must be embedded or process")
    return config
