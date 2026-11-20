"""The valves the instance holds, and the candidates a room flow offers."""

from __future__ import annotations

from homeassistant.config_entries import ConfigEntry
from homeassistant.core import HomeAssistant
from homeassistant.helpers import device_registry as dr
from homeassistant.helpers import entity_registry as er
from pydantic import BaseModel, ConfigDict

from .const import SUBENTRY_TYPE_ROOM

HIVE_MANUFACTURER = "Hive"
HIVE_VALVE_MODEL = "Radiator valve"
FEED_SUFFIX = "_external_measured_room_sensor"
LOCAL_TEMPERATURE_SUFFIX = "_local_temperature"
CLIMATE_DOMAIN = "climate"


class ValveCandidate(BaseModel):
    """One valve a room may name, and what the room form derives from it."""

    model_config = ConfigDict(frozen=True, extra="forbid")

    entity_id: str
    name: str
    area_id: str | None
    source_entity_id: str | None


def _object_id(entity_id: str) -> str:
    """Return the object id of one entity id."""
    return entity_id.split(".", 1)[1]


def _feed_entity_id(entity_id: str) -> str:
    """Return the calibration feed entity of one valve."""
    return f"number.{_object_id(entity_id)}{FEED_SUFFIX}"


def _source_entity_id(entity_id: str) -> str:
    """Return the candidate room temperature entity of one valve."""
    return f"sensor.{_object_id(entity_id)}{LOCAL_TEMPERATURE_SUFFIX}"


def _is_hive_valve(manufacturer: str | None, model: str | None) -> bool:
    """Return whether one device carries the Hive radiator valve."""
    return manufacturer == HIVE_MANUFACTURER and (model or "").startswith(HIVE_VALVE_MODEL)


def claimed_valves(entry: ConfigEntry, *, exclude_subentry_id: str | None = None) -> set[str]:
    """Return the valves the rooms of one entry already claim."""
    return {
        str(valve)
        for subentry in entry.subentries.values()
        if subentry.subentry_type == SUBENTRY_TYPE_ROOM
        and subentry.subentry_id != exclude_subentry_id
        for valve in subentry.data.get("valves", ())
    }


def available_valves(
    hass: HomeAssistant,
    entry: ConfigEntry,
    *,
    exclude_subentry_id: str | None = None,
) -> tuple[ValveCandidate, ...]:
    """Return the valves the room flow may offer for one entry.

    A candidate is a `climate` entity of a device that carries the Hive radiator valve,
    or a `climate` entity that owns the calibration feed entity. The valves the other
    rooms of the entry claim are left out, so one valve reaches one room.
    """
    devices = dr.async_get(hass)
    entities = er.async_get(hass)
    claimed = claimed_valves(entry, exclude_subentry_id=exclude_subentry_id)
    candidates: list[ValveCandidate] = []
    for entity in entities.entities.values():
        if entity.domain != CLIMATE_DOMAIN or entity.device_id is None:
            continue
        if entity.entity_id in claimed:
            continue
        device = devices.async_get(entity.device_id)
        if not isinstance(device, dr.DeviceEntry):
            # A child device carries no model and no area, so it is never a valve.
            continue
        owns_feed = entities.async_get(_feed_entity_id(entity.entity_id)) is not None
        if not (_is_hive_valve(device.manufacturer, device.model) or owns_feed):
            continue
        source = _source_entity_id(entity.entity_id)
        candidates.append(
            ValveCandidate(
                entity_id=entity.entity_id,
                name=entity.name or entity.original_name or entity.entity_id,
                area_id=device.area_id,
                source_entity_id=source if entities.async_get(source) is not None else None,
            )
        )
    return tuple(sorted(candidates, key=lambda candidate: candidate.entity_id))
