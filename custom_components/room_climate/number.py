"""The knobs of the simulation house."""

from __future__ import annotations

from homeassistant.components.number import NumberEntity
from homeassistant.core import HomeAssistant
from homeassistant.helpers.entity_platform import AddConfigEntryEntitiesCallback

from . import RoomClimateConfigEntry
from .const import SIM_MARKER
from .sim import (
    FEED_DISABLED,
    FEED_MAX,
    FEED_MIN,
    FEED_STEP,
    SIM_INITIAL_OUTDOOR_TEMPERATURE,
    SIM_INITIAL_TEMPERATURE,
    SimNumberEntity,
)


async def async_setup_entry(
    hass: HomeAssistant,
    entry: RoomClimateConfigEntry,
    async_add_entities: AddConfigEntryEntitiesCallback,
) -> None:
    """Add the temperature knobs of the bench, and none for a real house."""
    coordinator = entry.runtime_data
    if not coordinator.config.simulation:
        return
    entry_id = entry.entry_id
    entities: list[NumberEntity] = [
        SimNumberEntity(
            entry_id,
            f"{SIM_MARKER}outdoor_temperature",
            "Sim outdoor temperature",
            SIM_INITIAL_OUTDOOR_TEMPERATURE,
        )
    ]
    for room in coordinator.config.rooms:
        label = room.room_id.replace("_", " ")
        entities.extend(
            [
                SimNumberEntity(
                    entry_id,
                    f"{SIM_MARKER}{room.room_id}_room_temperature",
                    f"Sim {label} room temperature",
                    SIM_INITIAL_TEMPERATURE,
                ),
                SimNumberEntity(
                    entry_id,
                    f"{SIM_MARKER}{room.room_id}_valve_local_temperature",
                    f"Sim {label} valve local temperature",
                    SIM_INITIAL_TEMPERATURE,
                    offline_room=room.room_id,
                ),
                SimNumberEntity(
                    entry_id,
                    f"{SIM_MARKER}{room.room_id}_valve_external_measured_room_sensor",
                    f"Sim {label} valve external measured room sensor",
                    FEED_DISABLED,
                    minimum=FEED_MIN,
                    maximum=FEED_MAX,
                    step=FEED_STEP,
                    device_class=None,
                    unit=None,
                    offline_room=room.room_id,
                ),
            ]
        )
    async_add_entities(entities)
