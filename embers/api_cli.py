"""Command-line entry point for the configured Ember HTTP service."""

from __future__ import annotations

import argparse
import logging
import os

from .config import load_config
from .logging_config import configure_logging


def main(argv: list[str] | None = None) -> None:
    parser = argparse.ArgumentParser(description="Ember REST and MCP HTTP server")
    parser.add_argument("--config", help="path to an Ember TOML configuration file")
    parser.add_argument("--host", help="override api.host")
    parser.add_argument("--port", type=int, help="override api.port")
    args = parser.parse_args(argv)
    overrides = {}
    if args.host is not None:
        overrides["api.host"] = args.host
    if args.port is not None:
        overrides["api.port"] = args.port
    config = load_config(config_path=args.config, overrides=overrides)
    config.require_runtime_supported()
    if not config.api.rest_enabled and not config.api.mcp_enabled:
        parser.error("both api.rest_enabled and api.mcp_enabled are false")

    configure_logging(config.logging)
    if config.api.host not in {"127.0.0.1", "localhost", "::1"}:
        logging.getLogger("embers").warning(
            "Ember is listening beyond loopback; use a trusted network and "
            "keep authentication enabled")

    previous_config = os.environ.get("EMBER_CONFIG")
    if args.config is not None:
        os.environ["EMBER_CONFIG"] = args.config
    try:
        from . import api
    finally:
        if args.config is not None:
            if previous_config is None:
                os.environ.pop("EMBER_CONFIG", None)
            else:
                os.environ["EMBER_CONFIG"] = previous_config
    api._config = config
    import uvicorn
    uvicorn.run(api.app, host=config.api.host, port=config.api.port)


if __name__ == "__main__":
    main()
