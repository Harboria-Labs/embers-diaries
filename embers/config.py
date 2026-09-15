"""Typed configuration for settings Ember actually consumes.

Precedence is built-in defaults, TOML file, environment variables, then
explicit programmatic (or CLI) overrides.  Credentials are intentionally not
part of this model: agent tokens remain runtime inputs and are never loaded
from or written to a configuration file by this module.
"""

from __future__ import annotations

from dataclasses import dataclass
import os
from pathlib import Path
from typing import Any, Mapping, Sequence

try:  # Python 3.11+
    import tomllib
except ModuleNotFoundError:  # pragma: no cover - exercised on Python 3.10
    import tomli as tomllib


DEFAULT_CONFIG_PATH = Path("config.toml")
DEFAULT_STORAGE_PATH = Path("./ember_store")
_SUPPORTED_KEYS = {
    "storage": {"path"},
    "maintenance": {"interval_seconds", "namespaces"},
}


class ConfigError(ValueError):
    """Raised when a supported configuration value is invalid."""


@dataclass(frozen=True)
class StorageConfig:
    path: Path = DEFAULT_STORAGE_PATH


@dataclass(frozen=True)
class MaintenanceConfig:
    interval_seconds: int = 0
    namespaces: tuple[str, ...] = ("memories",)

    @property
    def enabled(self) -> bool:
        return self.interval_seconds > 0


@dataclass(frozen=True)
class EmberConfig:
    storage: StorageConfig = StorageConfig()
    maintenance: MaintenanceConfig = MaintenanceConfig()


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
    if not isinstance(loaded, dict):  # defensive; TOML roots are mappings
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
        unknown_keys = set(raw) - _SUPPORTED_KEYS[section]
        if unknown_keys:
            names = ", ".join(f"{section}.{key}" for key in sorted(unknown_keys))
            raise ConfigError(f"unsupported {source} setting(s): {names}")


def _storage_path(value: Any) -> Path:
    if not isinstance(value, (str, os.PathLike)) or isinstance(value, bytes):
        raise ConfigError("storage.path must be a non-empty path")
    text = os.fspath(value).strip()
    if not text:
        raise ConfigError("storage.path must be a non-empty path")
    return Path(text).expanduser()


def _interval(value: Any) -> int:
    if isinstance(value, bool):
        raise ConfigError("maintenance.interval_seconds must be an integer >= 0")
    try:
        parsed = int(value)
    except (TypeError, ValueError):
        raise ConfigError(
            "maintenance.interval_seconds must be an integer >= 0") from None
    if parsed < 0 or isinstance(value, float) and not value.is_integer():
        raise ConfigError("maintenance.interval_seconds must be an integer >= 0")
    return parsed


def _namespaces(value: Any) -> tuple[str, ...]:
    if isinstance(value, str):
        items: Sequence[Any] = value.split(",")
    elif isinstance(value, Sequence) and not isinstance(value, (bytes, bytearray)):
        items = value
    else:
        raise ConfigError(
            "maintenance.namespaces must be a TOML array or comma-separated string")
    namespaces: list[str] = []
    for item in items:
        if not isinstance(item, str):
            raise ConfigError("maintenance.namespaces entries must be strings")
        name = item.strip()
        if name and name not in namespaces:
            namespaces.append(name)
    if not namespaces:
        raise ConfigError("maintenance.namespaces must contain at least one namespace")
    return tuple(namespaces)


def load_config(
    config_path: str | os.PathLike[str] | None = None,
    *,
    env: Mapping[str, str] | None = None,
    storage_path: str | os.PathLike[str] | None = None,
    maintenance_interval_seconds: int | str | None = None,
    maintenance_namespaces: Sequence[str] | str | None = None,
) -> EmberConfig:
    """Load and validate Ember's currently supported configuration.

    ``config_path`` selects an explicit file and therefore must exist.  With no
    explicit path, ``EMBER_CONFIG`` may select a file; otherwise an optional
    ``config.toml`` in the current directory is used.  The final keyword
    arguments are explicit overrides suitable for a CLI or embedding program.
    """

    environment = os.environ if env is None else env
    configured_path = config_path
    if configured_path is None:
        configured_path = environment.get("EMBER_CONFIG")
    explicit_file = configured_path is not None
    path = Path(configured_path) if explicit_file else DEFAULT_CONFIG_PATH
    values = _read_toml(path, required=explicit_file)
    _validate_keys(values, f"configuration file {path}")

    storage_value: Any = values.get("storage", {}).get(
        "path", DEFAULT_STORAGE_PATH)
    interval_value: Any = values.get("maintenance", {}).get(
        "interval_seconds", 0)
    namespaces_value: Any = values.get("maintenance", {}).get(
        "namespaces", ("memories",))

    if "EMBER_STORE" in environment:
        storage_value = environment["EMBER_STORE"]
    if "EMBER_MAINTENANCE_INTERVAL_SECONDS" in environment:
        interval_value = environment["EMBER_MAINTENANCE_INTERVAL_SECONDS"]
    if "EMBER_MAINTENANCE_NAMESPACES" in environment:
        namespaces_value = environment["EMBER_MAINTENANCE_NAMESPACES"]

    if storage_path is not None:
        storage_value = storage_path
    if maintenance_interval_seconds is not None:
        interval_value = maintenance_interval_seconds
    if maintenance_namespaces is not None:
        namespaces_value = maintenance_namespaces

    return EmberConfig(
        storage=StorageConfig(path=_storage_path(storage_value)),
        maintenance=MaintenanceConfig(
            interval_seconds=_interval(interval_value),
            namespaces=_namespaces(namespaces_value),
        ),
    )
