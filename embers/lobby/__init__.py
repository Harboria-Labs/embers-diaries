"""Ephemeral shared-task board. Not Ember memory."""

from .context import resolve_namespace
from .store import LobbyStore, ALLOWED_ROOMS, ALLOWED_TYPES

__all__ = ["LobbyStore", "ALLOWED_ROOMS", "ALLOWED_TYPES", "resolve_namespace"]
