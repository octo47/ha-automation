"""The diagnostics of one house entry."""

from __future__ import annotations

from datetime import datetime
from typing import Any

from homeassistant.core import HomeAssistant

from . import RoomClimateConfigEntry


def _timestamp(value: datetime | None) -> str | None:
    """Return one moment as a JSON string."""
    return None if value is None else value.isoformat()


async def async_get_config_entry_diagnostics(
    hass: HomeAssistant, entry: RoomClimateConfigEntry
) -> dict[str, Any]:
    """Return the configuration, the last cycle and the boiler transitions of one house."""
    coordinator = entry.runtime_data
    return {
        "config": coordinator.config.model_dump(mode="json"),
        "output": (
            None if coordinator.output is None else coordinator.output.model_dump(mode="json")
        ),
        "watched_entities": list(coordinator.watched_entities),
        "lockout_active": coordinator.lockout_active,
        "master_enabled": coordinator.master_enabled,
        "override_active": coordinator.override_active,
        "boiler_on": coordinator.boiler_on,
        "boiler_state_since": _timestamp(coordinator.boiler_state_since),
        "last_command_at": {
            entity_id: _timestamp(moment)
            for entity_id, moment in coordinator.last_command_at.items()
        },
    }
