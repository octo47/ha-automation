"""The valves and the sensors the instance holds, and the candidates a room flow offers."""

from __future__ import annotations

from collections.abc import Mapping
from typing import Any, cast

from homeassistant.config_entries import ConfigEntry
from homeassistant.core import HomeAssistant
from homeassistant.helpers import device_registry as dr
from homeassistant.helpers import entity_registry as er
from pydantic import BaseModel, ConfigDict

from .const import CONF_SOURCES, SUBENTRY_TYPE_ROOM

HIVE_MANUFACTURER = "Hive"
HIVE_VALVE_MODEL = "Radiator valve"
FEED_SUFFIX = "_external_measured_room_sensor"
LOCAL_TEMPERATURE_SUFFIX = "_local_temperature"
CLIMATE_DOMAIN = "climate"
SENSOR_DOMAIN = "sensor"
TEMPERATURE_DEVICE_CLASS = "temperature"
DEVICE_TEMPERATURE_SUFFIX = "device_temperature"
SOURCE_ENTITY = "entity_id"


class ValveCandidate(BaseModel):
    """One valve a room may name, and what the room form derives from it."""

    model_config = ConfigDict(frozen=True, extra="forbid")

    entity_id: str
    name: str
    area_id: str | None
    source_entity_id: str | None


class SensorCandidate(BaseModel):
    """One temperature sensor a room may name as a source."""

    model_config = ConfigDict(frozen=True, extra="forbid")

    entity_id: str
    name: str
    area_id: str | None


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


def claimed_sources(entry: ConfigEntry, *, exclude_subentry_id: str | None = None) -> set[str]:
    """Return the temperature sources the rooms of one entry already claim."""
    claimed: set[str] = set()
    for subentry in entry.subentries.values():
        if subentry.subentry_type != SUBENTRY_TYPE_ROOM:
            continue
        if subentry.subentry_id == exclude_subentry_id:
            continue
        stored: list[Any] = list(subentry.data.get(CONF_SOURCES) or [])
        for source in stored:
            entity_id = _claimed_entity_id(source)
            if entity_id is not None:
                claimed.add(entity_id)
    return claimed


def _claimed_entity_id(source: Any) -> str | None:
    """Return the entity id a stored source names, or None when it names none.

    The stored payload is JSON, so the source arrives as an untyped mapping.
    """
    if isinstance(source, str):
        return source
    if not isinstance(source, Mapping):
        return None
    stored = cast("Mapping[str, Any]", source)
    entity_id = stored.get(SOURCE_ENTITY)
    return None if entity_id is None else str(entity_id)


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


def _source_area(entity: er.RegistryEntry, devices: dr.DeviceRegistry) -> str | None:
    """Return the area of one source: the area of its entity, or of its device."""
    if entity.area_id is not None:
        return entity.area_id
    if entity.device_id is None:
        return None
    device = devices.async_get(entity.device_id)
    return device.area_id if device is not None else None


def available_temperature_sources(
    hass: HomeAssistant,
    entry: ConfigEntry,
    *,
    first_area: str | None = None,
    exclude_subentry_id: str | None = None,
) -> tuple[SensorCandidate, ...]:
    """Return the temperature sensors the room flow may offer for one entry.

    A candidate is a `sensor` entity whose device class is `temperature`, read from
    the entity registry. An entity whose object id ends with `device_temperature`
    reports the hardware of a contact or a motion sensor and not a room, so it is
    left out. A source that another room of the entry claims is left out too. The
    sensors of `first_area` come first, so the sensor of the room's own area leads
    the list, and each group is ordered by entity id.
    """
    entities = er.async_get(hass)
    devices = dr.async_get(hass)
    claimed = claimed_sources(entry, exclude_subentry_id=exclude_subentry_id)
    candidates: list[SensorCandidate] = []
    for entity in entities.entities.values():
        if entity.domain != SENSOR_DOMAIN or entity.entity_id in claimed:
            continue
        device_class = entity.device_class or entity.original_device_class
        if device_class != TEMPERATURE_DEVICE_CLASS:
            continue
        if _object_id(entity.entity_id).endswith(DEVICE_TEMPERATURE_SUFFIX):
            continue
        candidates.append(
            SensorCandidate(
                entity_id=entity.entity_id,
                name=entity.name or entity.original_name or entity.entity_id,
                area_id=_source_area(entity, devices),
            )
        )
    ordered = sorted(
        candidates,
        key=lambda candidate: (candidate.area_id != first_area, candidate.entity_id),
    )
    return tuple(ordered)
