"""The valves and the sensors the instance holds, and the candidates a room flow offers."""

from __future__ import annotations

from collections.abc import Mapping, Sequence
from typing import Any, cast

from homeassistant.config_entries import ConfigEntry
from homeassistant.const import ATTR_DEVICE_CLASS
from homeassistant.core import HomeAssistant
from homeassistant.helpers import area_registry as ar
from homeassistant.helpers import device_registry as dr
from homeassistant.helpers import entity_registry as er
from pydantic import BaseModel, ConfigDict

from .const import CONF_SOURCES, DOMAIN, SUBENTRY_TYPE_ROOM

HIVE_MANUFACTURER = "Hive"
HIVE_VALVE_MODEL = "Radiator valve"
FEED_SUFFIX = "_external_measured_room_sensor"
LOCAL_TEMPERATURE_SUFFIX = "_local_temperature"
CLIMATE_DOMAIN = "climate"
SENSOR_DOMAIN = "sensor"
TEMPERATURE_DEVICE_CLASS = "temperature"
DEVICE_TEMPERATURE_SUFFIX = "device_temperature"
SOURCE_ENTITY = "entity_id"

LABEL_SEPARATOR = " · "

# A name built only of these words says nothing about which entity it is: the vendor of
# this house names ten of its sensors `Temperature`, and its derived sensors
# `Temperature EMA` and `Temperature EMA 1h`, and a Hive valve is a `Radiator valve`.
GENERIC_WORDS = frozenset(
    {"temperature", "humidity", "radiator", "valve", "trv", "ema", "1h", "6h", "12h", "24h"}
)


class ValveCandidate(BaseModel):
    """One valve a room may name, and what the room form derives from it."""

    model_config = ConfigDict(frozen=True, extra="forbid")

    entity_id: str
    name: str
    area_id: str | None
    area_name: str | None
    device_name: str | None
    source_entity_id: str | None


class SensorCandidate(BaseModel):
    """One temperature sensor a room may name as a source."""

    model_config = ConfigDict(frozen=True, extra="forbid")

    entity_id: str
    name: str
    area_id: str | None
    area_name: str | None
    device_name: str | None


def _is_generic(name: str) -> bool:
    """Return whether one entity name says nothing about which entity it is.

    A generic name repeats across the house, so an area and a device name are what tell
    two entities of that name apart.
    """
    words = name.casefold().split()
    return bool(words) and all(word in GENERIC_WORDS for word in words)


def _candidate_label(candidate: ValveCandidate | SensorCandidate) -> str:
    """Return the label one candidate would carry on its own.

    The label names the entity in the house and not only the entity: the area of the
    entity, then the name of its device when the name of the entity is generic, then the
    name of the entity. A generic name that neither an area nor a device name qualifies
    names nothing in the house, so the entity id stands instead.
    """
    parts: list[str] = []
    if candidate.area_name:
        parts.append(candidate.area_name)
    if _is_generic(candidate.name):
        if candidate.device_name:
            parts.append(candidate.device_name)
        if not parts:
            return candidate.entity_id
    return LABEL_SEPARATOR.join([*parts, candidate.name])


def picker_labels(
    candidates: Sequence[ValveCandidate | SensorCandidate],
) -> dict[str, str]:
    """Return the label of every entry of one picker, keyed by entity id.

    One function decides the label of the valve picker, the source picker and the weights
    step. Two entries that would read the same fall back to their entity ids, so no two
    rows of one picker read the same.
    """
    drafts = {candidate.entity_id: _candidate_label(candidate) for candidate in candidates}
    counts: dict[str, int] = {}
    for label in drafts.values():
        counts[label] = counts.get(label, 0) + 1
    return {
        entity_id: entity_id if counts[label] > 1 else label for entity_id, label in drafts.items()
    }


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
    areas = ar.async_get(hass)
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
                area_name=_area_name(areas, device.area_id),
                device_name=_device_name(device),
                source_entity_id=source if entities.async_get(source) is not None else None,
            )
        )
    return tuple(sorted(candidates, key=lambda candidate: candidate.entity_id))


def _area_name(areas: ar.AreaRegistry, area_id: str | None) -> str | None:
    """Return the name of one area, or None when there is no such area."""
    if area_id is None:
        return None
    area = areas.async_get_area(area_id)
    return area.name if area is not None else None


def _device_name(device: dr.AnyDeviceEntry | None) -> str | None:
    """Return the name of one device: the operator's name first, then the given name."""
    if device is None:
        return None
    return device.name_by_user or device.name


def _source_house(
    entity: er.RegistryEntry, devices: dr.DeviceRegistry
) -> tuple[str | None, str | None]:
    """Return the area id and the device name of one source.

    The area of the entity leads, and the area of its device is the fallback.
    """
    device = devices.async_get(entity.device_id) if entity.device_id is not None else None
    area_id = entity.area_id if entity.area_id is not None else _device_area(device)
    return area_id, _device_name(device)


def _device_area(device: dr.AnyDeviceEntry | None) -> str | None:
    """Return the area of one device, or None when there is no such device."""
    return device.area_id if device is not None else None


def _device_class(hass: HomeAssistant, entity: er.RegistryEntry) -> str | None:
    """Return the device class of one entity: the state of the entity first.

    The sensors of a house often declare the device class on their state and not in the
    entity registry, so the state leads the registry, which is the fallback.
    """
    state = hass.states.get(entity.entity_id)
    if state is not None:
        device_class = state.attributes.get(ATTR_DEVICE_CLASS)
        if device_class is not None:
            return str(device_class)
    return entity.device_class or entity.original_device_class


def _component_entry_ids(hass: HomeAssistant) -> set[str]:
    """Return the entry ids of this component, whose entities a room must not read."""
    return {entry.entry_id for entry in hass.config_entries.async_entries(DOMAIN)}


def available_temperature_sources(
    hass: HomeAssistant,
    entry: ConfigEntry,
    *,
    first_area: str | None = None,
    exclude_subentry_id: str | None = None,
) -> tuple[SensorCandidate, ...]:
    """Return the temperature sensors the room flow may offer for one entry.

    A candidate is a `sensor` entity whose device class is `temperature`, read from the
    state of the entity and then from the entity registry. An entity of this component is
    never a candidate, because a room that read it would read the output of the component
    as its own input. An entity whose object id ends with `device_temperature` reports the
    hardware of a contact or a motion sensor and not a room, so it is left out. A source
    that another room of the entry claims is left out too. The sensors of `first_area` come
    first, so the sensor of the room's own area leads the list, and each group is ordered by
    entity id.
    """
    entities = er.async_get(hass)
    devices = dr.async_get(hass)
    areas = ar.async_get(hass)
    claimed = claimed_sources(entry, exclude_subentry_id=exclude_subentry_id)
    own_entries = _component_entry_ids(hass)
    candidates: list[SensorCandidate] = []
    for entity in entities.entities.values():
        if entity.domain != SENSOR_DOMAIN or entity.entity_id in claimed:
            continue
        if entity.config_entry_id in own_entries:
            continue
        if _device_class(hass, entity) != TEMPERATURE_DEVICE_CLASS:
            continue
        if _object_id(entity.entity_id).endswith(DEVICE_TEMPERATURE_SUFFIX):
            continue
        area_id, device_name = _source_house(entity, devices)
        candidates.append(
            SensorCandidate(
                entity_id=entity.entity_id,
                name=entity.name or entity.original_name or entity.entity_id,
                area_id=area_id,
                area_name=_area_name(areas, area_id),
                device_name=device_name,
            )
        )
    ordered = sorted(
        candidates,
        key=lambda candidate: (candidate.area_id != first_area, candidate.entity_id),
    )
    return tuple(ordered)
